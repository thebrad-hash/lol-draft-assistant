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
from .scoring import DraftState, score_draft
from .store import Store
from .weights import dynamic_weights

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


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()  # raises if the DB hasn't been built yet
    return _store


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
    try:
        with _store_lock:  # one shared sqlite connection -> serialize queries
            results, _warnings = score_draft(
                get_store(), ds, rank=state.rank or config.DEFAULT_RANK, weights=weights
            )
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # engine components -> flat web contributions
    comp_specs = [
        ("in_lane", "in_lane_z", "counter", "enemy"),
        ("out_of_lane", "out_of_lane_z", "counter", "enemy"),
        ("synergy", "synergy_z", "synergy", "ally"),
    ]
    out = []
    for cs in results[: max(1, state.limit)]:
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
        out.append(
            {
                "championId": cs.champion,
                "championName": cs.champion,  # web resolves a display name locally
                "totalEv": round(cs.total, 3),
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
    EV of the best champion still available for each — i.e. where to pick next."""
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
            for role in open_roles:
                w = dynamic_weights(weights, role, enemies, allies)[0] if state.auto else weights
                ds = DraftState(my_role=role, enemies=enemies, allies=allies,
                                bans=list(state.bans), pool=state.poolFilter)
                results, _ = score_draft(store, ds, rank=rank, weights=w)
                if not results:
                    continue
                top = results[0]
                tail = results[min(2, len(results) - 1)]
                rows.append({
                    "role": ENGINE_TO_UI_ROLE.get(role, role),
                    "bestChamp": top.champion,
                    "bestEv": round(top.total, 3),
                    "top": [{"champ": r.champion, "ev": round(r.total, 3)} for r in results[:3]],
                    "urgency": round(top.total - tail.total, 3),  # drop-off best -> 3rd
                })
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    rows.sort(key=lambda x: x["bestEv"], reverse=True)
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
