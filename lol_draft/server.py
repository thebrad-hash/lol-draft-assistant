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

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config, lcu
from .scoring import DraftState, score_draft
from .store import Store

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


app = FastAPI(title="LoL Draft Assistant API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev: also reachable directly, not just via Vite proxy
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: Optional[Store] = None


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
        meta = get_store().meta()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"ok": True, "meta": meta}


@app.get("/api/live")
def live(demo: bool = False):
    """Live champ-select state from the local League client (read-only).
    Pass ?demo=1 for a synthetic payload to preview the feature without a game."""
    if demo:
        return lcu.demo_draft()
    return lcu.live_draft()


@app.post("/api/recommend")
def recommend(state: DraftStateIn):
    try:
        store = get_store()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

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
    results, _warnings = score_draft(
        store, ds, rank=state.rank or config.DEFAULT_RANK, weights=weights
    )

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


def main():
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
