"""FastAPI wrapper around the EV scoring engine.

Exposes the same contract the web UI's mock implements, so the frontend can
swap mock -> real with a one-line change. Translates at the boundary:
  - UI roles  BOT -> ADC, SUPPORT -> SUP   (and back for response roles)
  - weights   camelCase -> snake_case
  - engine CandidateScore -> web Recommendation

Run:  python -m lol_draft.server      (serves on http://127.0.0.1:8000)
      or:  uvicorn lol_draft.server:app --port 8000
Requires:  pip install fastapi uvicorn   (not needed for the CLI)
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, lcu, lobby_store
from .cellnoise import load_c
from .champstats import ChampStrength, default_strength_path
from .evaluate import evaluate_teams
from .features import FEATURE_NAMES
from .model import (DEFAULT_DATASET, WinProbEnsemble, WinProbModel,
                    discover_datasets, model_paths)
from .scoring import DraftState, score_draft
from .store import Store
from .weights import dynamic_weights
from .winprob import (TIE_THRESHOLD_DEFAULT, rank_candidates,
                      rank_candidates_uncertain)

# Distinguishability bar for the "tied with top pick" flag, tunable without a
# code change via the WINPROB_TIE_THRESHOLD env var (e.g. on Vercel).
try:
    TIE_THRESHOLD = float(os.environ.get("WINPROB_TIE_THRESHOLD", TIE_THRESHOLD_DEFAULT))
except ValueError:
    TIE_THRESHOLD = TIE_THRESHOLD_DEFAULT

# --- role mapping (UI <-> engine) ---
UI_TO_ENGINE_ROLE = {
    "TOP": "TOP",
    "JUNGLE": "JUNGLE",
    "MID": "MID",
    "BOT": "ADC",
    "SUPPORT": "SUP",
}
ENGINE_TO_UI_ROLE = {v: k for k, v in UI_TO_ENGINE_ROLE.items()}

# --- request models (mirror the web DraftState) ---
class WeightsIn(BaseModel):
    inLane: float = config.DEFAULT_WEIGHTS["in_lane"]
    outOfLane: float = config.DEFAULT_WEIGHTS["out_of_lane"]
    synergy: float = config.DEFAULT_WEIGHTS["synergy"]
    blindability: float = config.DEFAULT_WEIGHTS["blindability"]


class DraftStateIn(BaseModel):
    bans: list[str] = []
    myTeam: dict[str, str] = {}
    enemyTeam: dict[str, str] = {}
    pickingForRole: str
    poolFilter: Optional[list[str]] = None
    weights: WeightsIn = WeightsIn()
    rank: Optional[str] = None
    dataset: Optional[str] = None  # win-prob model dataset id ("all", "16.12", ...)
    limit: int = 15


class EvaluateIn(BaseModel):
    myTeam: dict[str, str] = {}
    enemyTeam: dict[str, str] = {}
    weights: WeightsIn = WeightsIn()
    rank: Optional[str] = None


app = FastAPI(title="LoL Draft Assistant API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev: also reachable directly, not just via Vite proxy
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: Optional[Store] = None
_store_lock = threading.Lock()  # serialize access to the one shared sqlite connection
# Win-prob models/ensembles are cached PER DATASET id ("all", "16.12", ...) as
# (file_mtime, object) so a RETRAINED model is picked up automatically on the next
# request without a server restart — the "grow over days" workflow drops a new
# winprob_patch_<P>.json in place and the live recommender swaps to it. A missing
# file caches as (None_mtime, None) and is cheaply re-stat'd each call.
_models: dict[str, tuple] = {}      # id -> (mtime|None, WinProbModel|None)
_ensembles: dict[str, tuple] = {}   # id -> (mtime|None, WinProbEnsemble|None)
_strengths: dict[str, tuple] = {}   # own-data champ_strength artifact (one file)
_models_lock = threading.Lock()
# champ->roles map is static (depends only on the built store), so compute it once.
_champ_roles_cache: Optional[dict] = None
# /api/live is served from a cache refreshed by a SINGLE BACKGROUND thread. The
# local-client read can block for seconds (PowerShell discovery, LCU HTTP), so a
# premade all polling Go Live would otherwise tie up a request thread each and
# wedge the whole server. By doing the read off the request path, every /api/live
# returns instantly (a dict read) and nothing can ever pile up on it.
_live_cache: dict = {"data": None}
_live_thread_started = False
_live_thread_lock = threading.Lock()
LIVE_REFRESH_S = 1.5  # background poll cadence


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()  # raises if the DB hasn't been built yet
    return _store


def _resolve_dataset(dataset: Optional[str]) -> str:
    """A request's dataset id, falling back to the default. Unknown ids fall back
    too (rather than 4xx) so a stale client toggle never breaks recommendations."""
    if dataset and (model_paths(dataset)[0].exists() or dataset == DEFAULT_DATASET):
        return dataset
    return DEFAULT_DATASET


def _cached_load(cache: dict, key: str, path, loader):
    """Return loader(path), cached by (path mtime). Reloads when the file changes
    on disk (retrain) and returns None when it's absent or unparseable — without
    re-reading an unchanged file on every request. Double-checked under the lock."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None  # file gone -> None object, but keep re-stat'ing cheaply
    entry = cache.get(key)
    if entry is not None and entry[0] == mtime:
        return entry[1]
    with _models_lock:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        entry = cache.get(key)
        if entry is None or entry[0] != mtime:
            obj = None
            if mtime is not None:
                try:
                    obj = loader(path)
                except (FileNotFoundError, ValueError):
                    obj = None
            cache[key] = (mtime, obj)
            entry = cache[key]
    return entry[1]


def get_model(dataset: str = DEFAULT_DATASET) -> Optional[WinProbModel]:
    """The calibrated win-probability model for `dataset`, cached by file mtime
    (auto-reloads on retrain). None if that dataset hasn't been trained yet
    (`python -m lol_draft.train`), in which case /api/recommend falls back to
    ranking by the legacy additive-z EV."""
    return _cached_load(_models, dataset, model_paths(dataset)[0], WinProbModel.load)


def get_ensemble(dataset: str = DEFAULT_DATASET) -> Optional[WinProbEnsemble]:
    """The bootstrap coefficient ensemble for `dataset` (1000 refits), cached by
    file mtime (auto-reloads on re-bootstrap). None if it hasn't been built yet
    (`python -m lol_draft.bootstrap`), in which case the recommender returns point
    estimates with no error bars. Pure-python inference keeps the serve path lean."""
    return _cached_load(_ensembles, dataset, model_paths(dataset)[1], WinProbEnsemble.load)


def get_strength(dataset: str = DEFAULT_DATASET):
    """The own-data ChampStrength artifact IF the dataset's model was trained
    with it (meta `champ_strength_source == "own_data_eb"`), else None. The
    model's meta — not a config flag — decides the feature source, so the serve
    path can never featurize differently from how the coefficients were fit.
    Cached by artifact mtime; a missing/unreadable artifact degrades to the
    machineloling win-rate path (slightly miscalibrated, never broken)."""
    model = get_model(dataset)
    if model is None or model.meta.get("champ_strength_source") != "own_data_eb":
        return None
    return _cached_load(_strengths, "own_data", default_strength_path(),
                        ChampStrength.load)


def _uncertainty(store: Store, ds: DraftState, rank: str,
                 dataset: str = DEFAULT_DATASET) -> Optional[list]:
    """Per-candidate bootstrap error bars + tie flags for one draft, or None if
    the ensemble isn't built. Caller must already hold `_store_lock`."""
    ens = get_ensemble(dataset)
    if ens is None:
        return None
    un, _ = rank_candidates_uncertain(
        store, ds, ens, rank=rank, point_model=get_model(dataset),
        tie_threshold=TIE_THRESHOLD, strength=get_strength(dataset),
        z_cell_c=load_c())  # z-cell noise (WS3): None (off) until the
    return un               # zcell_noise.json artifact ships / env overrides


def _map_roles(team: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for ui_role, champ in team.items():
        engine_role = UI_TO_ENGINE_ROLE.get(ui_role.upper())
        if engine_role and champ:
            out[engine_role] = champ
    return out


@app.get("/api/health")
def health():
    try:
        with _store_lock:
            meta = get_store().meta()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"ok": True, "meta": meta}


def _dataset_label(dataset_id: str, meta: dict) -> str:
    """Human label for the toggle: backbone as 'All patches', a plain patch as
    'Patch 16.12', and a rank-variant id like '16.12-emerald' as
    'Patch 16.12 · Emerald' (so the same patch can have apex vs Emerald models)."""
    if dataset_id == DEFAULT_DATASET:
        return "All patches"
    if "-" in dataset_id:
        patch, variant = dataset_id.split("-", 1)
        return f"Patch {patch} · {variant.capitalize()}"
    return f"Patch {dataset_id}"


@app.get("/api/models")
def models():
    """Win-prob model datasets available to the recommender, for the UI toggle.
    Each entry carries provenance (patch, #matches, out-of-sample AUC/log-loss,
    built_at) and whether its bootstrap ensemble is present (=> error bars). The
    `default` is what /api/recommend uses when a request omits `dataset`."""
    found = discover_datasets()
    out = []
    for ds_id, info in found.items():
        meta = info.get("meta", {})
        cv_ll = meta.get("cv_logloss")
        null_ll = meta.get("null_logloss")
        # Honest confidence flag: the calibrated model only adds value when its
        # out-of-sample log-loss actually beats the 50/50 null. Below that it's
        # fitting noise (typical under ~1k games) — the UI warns rather than
        # presenting a falsely precise number.
        beats_null = (cv_ll is not None and null_ll is not None and cv_ll < null_ll)
        out.append({
            "id": ds_id,
            "label": _dataset_label(ds_id, meta),
            "patch": meta.get("patch"),
            "rank": meta.get("rank"),
            "nMatches": meta.get("n_matches"),
            "nRows": meta.get("n_rows"),
            "cvAuc": meta.get("cv_auc"),
            "cvLogloss": cv_ll,
            "nullLogloss": null_ll,
            "baselineLogloss": meta.get("baseline_logloss"),
            "cvEce": meta.get("cv_ece"),
            "beatsNull": beats_null,
            "builtAt": meta.get("built_at"),
            "hasUncertainty": info["ensemble_path"].exists(),
        })
    # Backbone first, then patches newest-first (string sort is fine for NN.NN).
    out.sort(key=lambda d: (d["id"] != DEFAULT_DATASET, d["id"]), reverse=False)
    patches = [d for d in out if d["id"] != DEFAULT_DATASET]
    backbone = [d for d in out if d["id"] == DEFAULT_DATASET]
    patches.sort(key=lambda d: d["id"], reverse=True)
    ordered = backbone + patches
    return {"datasets": ordered, "default": DEFAULT_DATASET}


def _ui_champ_roles(store: Store) -> dict[str, list[str]]:
    """champion id -> playable UI roles, most-played first. Lets the live mapper
    infer roles for picks the client doesn't position: the enemy team (always
    hidden) and everyone in blind/quickplay/practice. Cached — the underlying
    store is static after build, so recomputing it on every /api/live poll just
    burned CPU under the store lock."""
    global _champ_roles_cache
    if _champ_roles_cache is not None:
        return _champ_roles_cache
    rank = store.settings().get("default_rank", config.DEFAULT_RANK)
    scored: dict[str, list[tuple[float, str]]] = {}
    for engine_role in config.ROLES:
        prs = store.pick_rates(rank, engine_role)
        for champ in store.role_champions(engine_role):
            ui_role = ENGINE_TO_UI_ROLE.get(engine_role, engine_role)
            scored.setdefault(champ, []).append((prs.get(champ, 0.0), ui_role))
    _champ_roles_cache = {c: [r for _, r in sorted(v, key=lambda t: t[0], reverse=True)]
                          for c, v in scored.items()}
    return _champ_roles_cache


def _live_refresh_loop():
    """Background daemon: refresh the live-state cache off the request path so the
    (possibly blocking) local-client read never occupies a request thread."""
    while True:
        try:
            champ_roles = None
            try:
                with _store_lock:
                    champ_roles = _ui_champ_roles(get_store())
            except Exception:
                pass
            _live_cache["data"] = lcu.live_draft(champ_roles=champ_roles)
        except Exception:
            pass
        time.sleep(LIVE_REFRESH_S)


def _ensure_live_thread():
    global _live_thread_started
    if _live_thread_started:
        return
    with _live_thread_lock:
        if _live_thread_started:
            return
        threading.Thread(target=_live_refresh_loop, daemon=True, name="live-refresh").start()
        _live_thread_started = True


def _is_local_request(request: Request) -> bool:
    """True when the caller is on the SAME machine as the server (the 'host').

    /api/live reads the server's *local* League client (127.0.0.1), so only the
    host can meaningfully use it; friends reach us through a tunnel. Cloudflare
    (and any reverse proxy) injects forwarding headers — their presence means the
    request is remote. Absent those, a loopback client address is the host."""
    h = request.headers
    if h.get("x-forwarded-for") or h.get("cf-connecting-ip") or h.get("x-real-ip"):
        return False
    client = request.client.host if request.client else ""
    return client in ("127.0.0.1", "::1", "localhost")


@app.get("/api/live")
def live(request: Request, demo: bool = False):
    """Live champ-select state from the local League client (read-only).
    Pass ?demo=1 for a synthetic payload to preview the feature without a game.

    Returns INSTANTLY from a cache that a single background thread refreshes — the
    slow/blocking client read never runs in the request handler, so any number of
    friends can poll this without starving the rest of the server.

    `isLocal` tells the caller whether THIS request is the host (we can read its
    client) or a remote friend (who should instead follow the lobby broadcast)."""
    is_local = _is_local_request(request)
    if demo:
        return {**lcu.demo_draft(), "isLocal": is_local}
    _ensure_live_thread()
    data = _live_cache["data"] or {
        "connected": False, "inChampSelect": False, "reason": "connecting"}
    return {**data, "isLocal": is_local}


@app.post("/api/recommend")
def recommend(state: DraftStateIn):
    my_role = UI_TO_ENGINE_ROLE.get(state.pickingForRole.upper())
    if my_role is None:
        raise HTTPException(status_code=400, detail=f"Unknown role {state.pickingForRole!r}")

    weights = {
        "in_lane": state.weights.inLane,
        "out_of_lane": state.weights.outOfLane,
        "synergy": state.weights.synergy,
        "blindability": state.weights.blindability,
    }
    ds = DraftState(
        my_role=my_role,
        enemies=_map_roles(state.enemyTeam),
        allies=_map_roles(state.myTeam),  # engine drops the my_role entry itself
        bans=list(state.bans),
        pool=state.poolFilter,
    )
    rank = state.rank or config.DEFAULT_RANK
    dataset = _resolve_dataset(state.dataset)
    try:
        with _store_lock:  # one shared sqlite connection -> serialize queries
            store = get_store()
            results, _warnings = score_draft(store, ds, rank=rank, weights=weights)
            model = get_model(dataset)
            wp_results = []
            un_results = None
            if model is not None:  # rank_candidates also hits the store -> same lock
                wp_results, _ = rank_candidates(store, ds, model, rank=rank,
                                                strength=get_strength(dataset))
                un_results = _uncertainty(store, ds, rank, dataset)  # bootstrap CIs if built
            return _format_picks(results, wp_results, model, state.limit, un_results)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/weights")
def auto_weights(state: DraftStateIn):
    """Context-adaptive weights for the role on the clock given both teams' picks.
    Pure (no store) — returns adjusted weights (camelCase) + readable notes."""
    my_role = UI_TO_ENGINE_ROLE.get(state.pickingForRole.upper())
    if my_role is None:
        raise HTTPException(status_code=400, detail=f"Unknown role {state.pickingForRole!r}")
    base = {
        "in_lane": state.weights.inLane,
        "out_of_lane": state.weights.outOfLane,
        "synergy": state.weights.synergy,
        "blindability": state.weights.blindability,
    }
    w, notes = dynamic_weights(base, my_role, _map_roles(state.enemyTeam), _map_roles(state.myTeam))
    return {
        "weights": {
            "inLane": w["in_lane"],
            "outOfLane": w["out_of_lane"],
            "synergy": w["synergy"],
            "blindability": w["blindability"],
        },
        "notes": notes,
    }


@app.post("/api/evaluate")
def evaluate(state: EvaluateIn):
    """Score a full 5v5 once both teams are locked, with win conditions.
    Mirrors the recommend contract (UI roles BOT/SUPPORT + camelCase weights)."""
    weights = {
        "in_lane": state.weights.inLane,
        "out_of_lane": state.weights.outOfLane,
        "synergy": state.weights.synergy,
        "blindability": state.weights.blindability,
    }
    role_label = {"TOP": "Top", "JUNGLE": "Jungle", "MID": "Mid",
                  "ADC": "Bot", "SUP": "Support"}
    try:
        with _store_lock:  # one shared sqlite connection -> serialize queries
            ev = evaluate_teams(
                get_store(), _map_roles(state.myTeam), _map_roles(state.enemyTeam),
                rank=state.rank or config.DEFAULT_RANK, weights=weights,
                label_a="Your team", label_b="Enemy", role_label=role_label,
            )
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    # engine role keys -> UI role keys in the structured payload
    for ln in ev["lanes"]:
        ln["role"] = ENGINE_TO_UI_ROLE.get(ln["role"], ln["role"])
    for sw in ev["swings"]["aBest"] + ev["swings"]["aWorst"]:
        sw["aRole"] = ENGINE_TO_UI_ROLE.get(sw["aRole"], sw["aRole"])
        sw["bRole"] = ENGINE_TO_UI_ROLE.get(sw["bRole"], sw["bRole"])
    return ev


class PickOrderIn(BaseModel):
    myTeam: dict[str, str] = {}
    enemyTeam: dict[str, str] = {}
    bans: list[str] = []
    poolFilter: Optional[list[str]] = None
    weights: WeightsIn = WeightsIn()
    rank: Optional[str] = None
    dataset: Optional[str] = None  # win-prob model dataset id ("all", "16.12", ...)
    auto: bool = False  # context-adaptive weights computed per open role


@app.post("/api/pick-order")
def pick_order(state: PickOrderIn):
    """Given picks + bans on both teams, rank the OPEN roles on your team by the
    best still-available champion for each — i.e. where to pick next. Uses the
    calibrated win-probability model when available (the additive-z `weights` /
    `auto` are then irrelevant — the model owns the weighting); else falls back
    to the additive-z EV."""
    weights = {
        "in_lane": state.weights.inLane,
        "out_of_lane": state.weights.outOfLane,
        "synergy": state.weights.synergy,
        "blindability": state.weights.blindability,
    }
    allies = _map_roles(state.myTeam)
    enemies = _map_roles(state.enemyTeam)
    open_roles = [r for r in config.ROLES if r not in allies]
    rank = state.rank or config.DEFAULT_RANK
    dataset = _resolve_dataset(state.dataset)
    rows = []
    try:
        with _store_lock:  # one shared sqlite connection -> serialize queries
            store = get_store()
            model = get_model(dataset)
            for role in open_roles:
                ds = DraftState(my_role=role, enemies=enemies, allies=allies,
                                bans=list(state.bans), pool=state.poolFilter)
                if model is not None:
                    cands, _ = rank_candidates(store, ds, model, rank=rank,
                                               strength=get_strength(dataset))
                    if not cands:
                        continue
                    best, tail = cands[0], cands[min(2, len(cands) - 1)]
                    rows.append({
                        "role": ENGINE_TO_UI_ROLE.get(role, role),
                        "bestChamp": best.champion,
                        "bestWin": round(best.win_prob, 4),
                        "bestEv": None,
                        "top": [{"champ": c.champion, "win": round(c.win_prob, 4)} for c in cands[:3]],
                        "urgency": round(best.win_prob - tail.win_prob, 4),  # win-prob drop best -> 3rd
                        "_sort": best.win_prob,
                    })
                else:
                    w = dynamic_weights(weights, role, enemies, allies)[0] if state.auto else weights
                    results, _ = score_draft(store, ds, rank=rank, weights=w)
                    if not results:
                        continue
                    top, tail = results[0], results[min(2, len(results) - 1)]
                    rows.append({
                        "role": ENGINE_TO_UI_ROLE.get(role, role),
                        "bestChamp": top.champion,
                        "bestWin": None,
                        "bestEv": round(top.total, 3),
                        "top": [{"champ": r.champion, "ev": round(r.total, 3)} for r in results[:3]],
                        "urgency": round(top.total - tail.total, 3),  # EV drop best -> 3rd
                        "_sort": top.total,
                    })
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    rows.sort(key=lambda x: x["_sort"], reverse=True)
    for x in rows:
        del x["_sort"]
    return {"openRoles": rows, "suggested": rows[0]["role"] if rows else None}


def _format_picks(results, wp_results, model, limit: int, un_results=None) -> list:
    """engine results -> web Recommendation dicts. Ranks by MEDIAN bootstrap
    win-prob when the ensemble is available (with error bars + tie flags), else
    by the point win-prob, else by additive-z EV. Single formatter shared by
    /api/recommend and /api/board so both surfaces stay in lockstep."""
    comp_specs = [
        ("in_lane", "in_lane_z", "counter", "enemy"),
        ("out_of_lane", "out_of_lane_z", "counter", "enemy"),
        ("synergy", "synergy_z", "synergy", "ally"),
    ]
    by_champ = {cs.champion: cs for cs in results}
    wp_by_champ = {r.champion: r for r in wp_results}
    un_by_champ = {u.champion: u for u in (un_results or [])}
    if un_results:
        order = [u.champion for u in un_results]          # rank by median
    elif model is not None:
        order = [r.champion for r in wp_results]
    else:
        order = [cs.champion for cs in results]
    # Field reference for a RELATIVE "vs an average pick for this role" delta,
    # computed over the FULL candidate pool (before truncating to `limit`). This
    # lets a pick read as above/below the field even when the whole board sits
    # below 50% — e.g. when locked teammates put the team at a baseline deficit no
    # single pick can erase, the best available pick still shows a positive delta.
    if un_results:
        _field = [u.median for u in un_results]
    elif wp_results:
        _field = [r.win_prob for r in wp_results]
    else:
        _field = []
    field_mean = sum(_field) / len(_field) if _field else None
    out = []
    for champ in order[: max(1, limit)]:
        cs = by_champ.get(champ)
        if cs is None:
            continue
        contribs = []
        for key, metric, kind, side in comp_specs:
            for c in cs.components[key].contributions:
                contribs.append({
                    "kind": kind, "targetChampion": c.name,
                    "targetRole": ENGINE_TO_UI_ROLE.get(c.role, c.role),
                    "side": side, "metric": metric, "value": round(c.z, 3),
                })
        contribs.sort(key=lambda x: abs(x["value"]), reverse=True)
        bz = cs.components["blindability"].value
        wp = wp_by_champ.get(champ)
        u = un_by_champ.get(champ)
        # the win-prob metric this pick is displayed/ranked by (median if we have
        # the bootstrap, else the point estimate); its delta vs the role's field.
        metric = u.median if u is not None else (wp.win_prob if wp is not None else None)
        delta = (metric - field_mean) if (metric is not None and field_mean is not None) else None
        rec = {
            "championId": champ, "championName": champ,
            "totalEv": round(cs.total, 3),
            "winProb": round(wp.win_prob, 4) if wp is not None else None,
            "features": ({k: round(wp.features[k], 3) for k in FEATURE_NAMES} if wp is not None else None),
            "contributions": contribs,
            "blindabilityZ": round(bz, 3) if bz is not None else None,
            "winProbField": round(field_mean, 4) if field_mean is not None else None,
            "winProbDelta": round(delta, 4) if delta is not None else None,
        }
        if u is not None:
            rec.update({
                "winProbMedian": round(u.median, 4),
                "winProbLo": round(u.p_lo, 4),
                "winProbHi": round(u.p_hi, 4),
                "winProbStd": round(u.std, 4),
                "ciLoPct": u.lo_pct, "ciHiPct": u.hi_pct,
                "tiedWithTop": u.tied_with_top,
                "probTopBetter": (round(u.prob_top_better, 3)
                                  if u.prob_top_better is not None else None),
                "tieThreshold": TIE_THRESHOLD,
            })
        out.append(rec)
    return out


class BoardIn(BaseModel):
    myTeam: dict[str, str] = {}
    enemyTeam: dict[str, str] = {}
    bans: list[str] = []
    weights: WeightsIn = WeightsIn()
    rank: Optional[str] = None
    dataset: Optional[str] = None  # win-prob model dataset id ("all", "16.12", ...)
    auto: bool = False
    limit: int = 4


@app.post("/api/board")
def board(state: BoardIn):
    """Top picks for EVERY role given both teams' picks (the all-roles board).
    Same scoring as /api/recommend per role; roles your team has already locked
    come back as {picked} with an empty list."""
    weights = {
        "in_lane": state.weights.inLane,
        "out_of_lane": state.weights.outOfLane,
        "synergy": state.weights.synergy,
        "blindability": state.weights.blindability,
    }
    allies = _map_roles(state.myTeam)
    enemies = _map_roles(state.enemyTeam)
    rank = state.rank or config.DEFAULT_RANK
    dataset = _resolve_dataset(state.dataset)
    out = []
    try:
        with _store_lock:  # one shared sqlite connection -> serialize queries
            store = get_store()
            model = get_model(dataset)
            for role in config.ROLES:
                ui = ENGINE_TO_UI_ROLE.get(role, role)
                if allies.get(role):  # already locked on my team
                    out.append({"role": ui, "picked": allies[role], "picks": []})
                    continue
                w = dynamic_weights(weights, role, enemies, allies)[0] if state.auto else weights
                ds = DraftState(my_role=role, enemies=enemies, allies=allies, bans=list(state.bans))
                results, _ = score_draft(store, ds, rank=rank, weights=w)
                wp_results = []
                un_results = None
                if model is not None:
                    wp_results, _ = rank_candidates(store, ds, model, rank=rank,
                                                    strength=get_strength(dataset))
                    un_results = _uncertainty(store, ds, rank, dataset)
                out.append({"role": ui, "picked": None,
                            "picks": _format_picks(results, wp_results, model, state.limit, un_results)})
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"roles": out}


# --- premade lobby: persisted via lobby_store (Redis on Vercel, in-memory locally)
# so a shared lobby survives serverless invocations. Same REST contract as before.
class MemberIn(BaseModel):
    memberId: str
    name: str = "Player"
    role: Optional[str] = None      # UI role (TOP/JUNGLE/MID/BOT/SUPPORT) or None
    pool: list[str] = []            # champion ids the player wants to play


@app.post("/api/lobby")
def create_lobby():
    """Create an empty lobby; the client shares <origin>/?lobby=<id>."""
    return lobby_store.create(secrets.token_urlsafe(6))


@app.get("/api/lobby/{lobby_id}")
def get_lobby(lobby_id: str):
    view = lobby_store.get(lobby_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Lobby not found")
    return view


@app.put("/api/lobby/{lobby_id}/member")
def upsert_member(lobby_id: str, member: MemberIn):
    """Add or update a member's name / role / champion pool (clients poll GET)."""
    data = {
        "name": (member.name or "Player")[:24],
        "role": member.role,
        "pool": list(dict.fromkeys(member.pool))[:30],  # de-dupe, cap
    }
    view = lobby_store.upsert_member(lobby_id, member.memberId, data)
    if view is None:
        raise HTTPException(status_code=404, detail="Lobby not found")
    return view


@app.delete("/api/lobby/{lobby_id}/member/{member_id}")
def leave_lobby(lobby_id: str, member_id: str):
    view = lobby_store.remove_member(lobby_id, member_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Lobby not found")
    return view


class ChatIn(BaseModel):
    memberId: str
    name: str = "Player"
    text: str


@app.post("/api/lobby/{lobby_id}/chat")
def post_chat(lobby_id: str, msg: ChatIn):
    """Append a chat message; clients pick it up on the next lobby poll (the view
    returned by GET /api/lobby/{id} now includes `messages`)."""
    view = lobby_store.post_message(lobby_id, msg.memberId, msg.name, msg.text)
    if view is None:
        raise HTTPException(status_code=404, detail="Lobby not found")
    return view


class LiveDraftIn(BaseModel):
    bans: list[str] = []
    myTeam: dict[str, str] = {}
    enemyTeam: dict[str, str] = {}
    pickingForRole: Optional[str] = None  # left null for broadcast; friends supply their own


class LivePublishIn(BaseModel):
    # null clears the broadcast (the source left champ select / went offline)
    draft: Optional[LiveDraftIn] = None
    memberId: Optional[str] = None   # who is broadcasting (any member may be the source)
    name: Optional[str] = None


@app.put("/api/lobby/{lobby_id}/live")
def publish_live(lobby_id: str, body: LivePublishIn):
    """Any member in champ select pushes its live draft into the lobby so every
    other member's app can auto-fill it — each overlaying their OWN role. The
    shared draft carries no per-player `pickingForRole`. Clients see it (with a
    server freshness stamp + the source member) in the GET /api/lobby/{id} view's
    `liveDraft`/`liveAt`/`liveSource`. No designated host: whoever is in the game
    is the source; on a cloud deploy a local broadcaster pushes here."""
    draft = None
    source = None
    if body.draft is not None:
        d = body.draft
        draft = {
            "bans": list(dict.fromkeys(d.bans))[:10],
            "myTeam": d.myTeam,
            "enemyTeam": d.enemyTeam,
            "pickingForRole": None,
        }
        source = {"memberId": body.memberId, "name": (body.name or "A teammate")[:24]}
    view = lobby_store.set_live_draft(lobby_id, draft, source)
    if view is None:
        raise HTTPException(status_code=404, detail="Lobby not found")
    return view


# --- serve the built web UI on this same port (tunnel-friendly) ---
# After `cd web && npm run build`, the whole app is reachable here, so a single
# tunnel to this port lets friends open the lobby link. Mounted last so it never
# shadows the /api/* routes above.
_DIST = config.PROJECT_DIR / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")


def main():
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
