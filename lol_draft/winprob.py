"""Rank champ-select candidates by calibrated win probability (Step 5).

This is the win-probability replacement for the additive-z `score_draft`: for
each available candidate in the open role, it completes our team with that
candidate, builds the team feature vector via the SAME `features.draft_features`
used in training, and reads off P(win) from the fitted `WinProbModel`.

Candidate set / availability rules mirror `scoring.score_draft` exactly (bans,
already-picked, pool filter), so this is a drop-in for the recommender — the
only thing that changes is the ranking key: a calibrated probability instead of
a unitless weighted-z sum.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .features import draft_features
from .model import WinProbModel
from .scoring import DraftState
from .store import Store


@dataclass
class CandidateWinProb:
    champion: str
    win_prob: float                       # calibrated P(win) if we pick this champ
    features: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)  # logit push per feature
    coverage: dict[str, int] = field(default_factory=dict)
    complete: bool = False                # was every pairing's cell present


def rank_candidates(store: Store, state: DraftState, model: WinProbModel,
                    *, rank: str | None = None) -> tuple[list[CandidateWinProb], list[str]]:
    """Return (candidates ranked by P(win) desc, warnings)."""
    rank = rank or config.DEFAULT_RANK
    my_role = state.my_role
    warnings: list[str] = []
    valid = store.all_champions()

    for role, champ in {**state.enemies, **state.allies}.items():
        if champ not in valid:
            warnings.append(f"Unknown champion {champ!r} (role {role}); ignored where unmatched.")
    allies = {r: c for r, c in state.allies.items() if r != my_role}
    if any(r == my_role for r in state.allies):
        warnings.append(f"Ally listed in my own role ({my_role}); ignored.")
    enemies = dict(state.enemies)

    candidates = store.role_champions(my_role)
    unavailable = set(state.bans) | set(enemies.values()) | set(allies.values())
    candidates = [c for c in candidates if c not in unavailable]
    if state.pool is not None:
        poolset = set(state.pool)
        for m in sorted(poolset - set(store.role_champions(my_role))):
            warnings.append(f"Pool champion {m!r} not playable in {my_role}; skipped.")
        candidates = [c for c in candidates if c in poolset]

    results: list[CandidateWinProb] = []
    for c in candidates:
        my_team = {**allies, my_role: c}
        f = draft_features(store, my_team, enemies, rank)
        results.append(CandidateWinProb(
            champion=c,
            win_prob=model.predict_proba(f.values),
            features=f.values,
            contributions=model.contributions(f.values),
            coverage=f.coverage,
            complete=f.complete,
        ))
    results.sort(key=lambda r: r.win_prob, reverse=True)
    return results, warnings
