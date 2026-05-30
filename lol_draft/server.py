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

import secrets
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, lcu
from .evaluate import evaluate_teams
from .features import FEATURE_NAMES
from .model import WinProbModel, default_model_path
from .scoring import DraftState, score_draft
from .store import Store
from .weights import dynamic_weights
from .winprob import rank_candidates

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
_model: Optional[WinProbModel] = None
_model_loaded = False


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()  # raises if the DB hasn't been built yet
    return _store


def get_model() -> Optional[WinProbModel]:
    """The calibrated win-probability model, loaded once. None if it hasn't been
    trained yet (`python -m lol_draft.train`), in which case /api/recommend falls
    back to ranking by the legacy additive-z EV."""
    global _model, _model_loaded
    if not _model_loaded:
        try:
            _model = WinProbModel.load(default_model_path())
        except (FileNotFoundError, ValueError):
            _model = None
        _model_loaded = True
    return _model


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


def _ui_champ_roles(store: Store) -> dict[str, list[str]]:
    """champion id -> playable UI roles, most-played first. Lets the live mapper
    infer roles for picks the client doesn't position: the enemy team (always
    hidden) and everyone in blind/quickplay/practice."""
    rank = store.settings().get("default_rank", config.DEFAULT_RANK)
    scored: dict[str, list[tuple[float, str]]] = {}
    for engine_role in config.ROLES:
        prs = store.pick_rates(rank, engine_role)
        for champ in store.role_champions(engine_role):
            ui_role = ENGINE_TO_UI_ROLE.get(engine_role, engine_role)
            scored.setdefault(champ, []).append((prs.get(champ, 0.0), ui_role))
    return {c: [r for _, r in sorted(v, key=lambda t: t[0], reverse=True)]
            for c, v in scored.items()}


@app.get("/api/live")
def live(demo: bool = False):
    """Live champ-select state from the local League client (read-only).
    Pass ?demo=1 for a synthetic payload to preview the feature without a game."""
    if demo:
        return lcu.demo_draft()
    champ_roles = None
    try:
        with _store_lock:
            champ_roles = _ui_champ_roles(get_store())
    except FileNotFoundError:
        pass  # store not built yet; live sync still works, sans role inference
    return lcu.live_draft(champ_roles=champ_roles)


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
    try:
        with _store_lock:  # one shared sqlite connection -> serialize queries
            store = get_store()
            results, _warnings = score_draft(store, ds, rank=rank, weights=weights)
            model = get_model()
            wp_results = []
            if model is not None:  # rank_candidates also hits the store -> same lock
                wp_results, _ = rank_candidates(store, ds, model, rank=rank)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # engine components -> flat web contributions (kept for the per-pick "why")
    comp_specs = [
        ("in_lane", "in_lane_z", "counter", "enemy"),
        ("out_of_lane", "out_of_lane_z", "counter", "enemy"),
        ("synergy", "synergy_z", "synergy", "ally"),
    ]
    by_champ = {cs.champion: cs for cs in results}
    wp_by_champ = {r.champion: r for r in wp_results}
    # rank by calibrated win probability when the model is available; else by EV
    order = ([r.champion for r in wp_results] if model is not None
             else [cs.champion for cs in results])

    out = []
    for champ in order[: max(1, state.limit)]:
        cs = by_champ.get(champ)
        if cs is None:
            continue
        contribs = []
        for key, metric, kind, side in comp_specs:
            for c in cs.components[key].contributions:
                contribs.append(
                    {
                        "kind": kind,
                        "targetChampion": c.name,
                        "targetRole": ENGINE_TO_UI_ROLE.get(c.role, c.role),
                        "side": side,
                        "metric": metric,
                        "value": round(c.z, 3),
                    }
                )
        contribs.sort(key=lambda x: abs(x["value"]), reverse=True)
        bz = cs.components["blindability"].value
        wp = wp_by_champ.get(champ)
        out.append(
            {
                "championId": champ,
                "championName": champ,  # web resolves a display name locally
                "totalEv": round(cs.total, 3),
                "winProb": round(wp.win_prob, 4) if wp is not None else None,
                "features": ({k: round(wp.features[k], 3) for k in FEATURE_NAMES}
                             if wp is not None else None),
                "contributions": contribs,
                "blindabilityZ": round(bz, 3) if bz is not None else None,
            }
        )
    return out


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
    rows = []
    try:
        with _store_lock:  # one shared sqlite connection -> serialize queries
            store = get_store()
            model = get_model()
            for role in open_roles:
                ds = DraftState(my_role=role, enemies=enemies, allies=allies,
                                bans=list(state.bans), pool=state.poolFilter)
                if model is not None:
                    cands, _ = rank_candidates(store, ds, model, rank=rank)
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


# --- premade lobby (in-memory; one host process, friends poll over the link) ---
_lobbies: dict[str, dict] = {}
_lobby_lock = threading.Lock()


class MemberIn(BaseModel):
    memberId: str
    name: str = "Player"
    role: Optional[str] = None      # UI role (TOP/JUNGLE/MID/BOT/SUPPORT) or None
    pool: list[str] = []            # champion ids the player wants to play


def _lobby_view(lobby: dict) -> dict:
    return {
        "id": lobby["id"],
        "members": [{"memberId": mid, **m} for mid, m in lobby["members"].items()],
    }


@app.post("/api/lobby")
def create_lobby():
    """Create an empty lobby; the client shares <origin>/?lobby=<id>."""
    lid = secrets.token_urlsafe(6)
    with _lobby_lock:
        _lobbies[lid] = {"id": lid, "members": {}}
    return {"id": lid}


@app.get("/api/lobby/{lobby_id}")
def get_lobby(lobby_id: str):
    with _lobby_lock:
        lobby = _lobbies.get(lobby_id)
        if lobby is None:
            raise HTTPException(status_code=404, detail="Lobby not found")
        return _lobby_view(lobby)


@app.put("/api/lobby/{lobby_id}/member")
def upsert_member(lobby_id: str, member: MemberIn):
    """Add or update a member's name / role / champion pool (clients poll GET)."""
    with _lobby_lock:
        lobby = _lobbies.get(lobby_id)
        if lobby is None:
            raise HTTPException(status_code=404, detail="Lobby not found")
        lobby["members"][member.memberId] = {
            "name": (member.name or "Player")[:24],
            "role": member.role,
            "pool": list(dict.fromkeys(member.pool))[:30],  # de-dupe, cap
        }
        return _lobby_view(lobby)


@app.delete("/api/lobby/{lobby_id}/member/{member_id}")
def leave_lobby(lobby_id: str, member_id: str):
    with _lobby_lock:
        lobby = _lobbies.get(lobby_id)
        if lobby is None:
            raise HTTPException(status_code=404, detail="Lobby not found")
        lobby["members"].pop(member_id, None)
        return _lobby_view(lobby)


# --- serve the built web UI on this same port (tunnel-friendly) ---
# After `cd web && npm run build`, the whole app is reachable here, so a single
# tunnel to this port lets friends open the lobby link. Mounted last so it never
# shadows the /api/* routes above.
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")


def main():
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
