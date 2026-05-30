"""Draft -> feature vector, shared by training and live inference.

This is the SINGLE source of truth for the model's features. The live
recommender (Step 5) and the training pipeline (Step 3) both call
`draft_features`, so a draft is turned into numbers exactly one way — no
train/inference skew.

A row is one team's view of a (possibly partial) draft:

    allies  = {role: champ}   # the team we're computing P(win) for
    enemies = {role: champ}

Features (all from the allies' perspective, ordered by FEATURE_NAMES):

    lane_z          mean matchup z over the 5 head-to-head lanes (same-role)
    counter_z       mean matchup z over off-role pairings (cross-role)
    synergy_z       mean(ally synergy z) - mean(enemy synergy z)
    champ_strength  mean(ally win_rate) - mean(enemy win_rate)

Why these, and the mapping to the scraped data
----------------------------------------------
The static data exposes only two relation modes, `matchup` and `synergy`, plus
per-(rank,role,champ) `win_rate`. So:
  - "lane" and "counter" both come from `matchup` — lane = same-role head-to-head,
    counter = cross-role (how your champs match the rest of their comp).
  - Matchup z is (near-)antisymmetric, so lane_z / counter_z are already RELATIVE
    (your edge == minus their edge). Synergy and win_rate are each team's own, so
    we take the DIFFERENCE to make them relative.
  - `blindability` is deliberately excluded: it's a blind-PICK safety heuristic,
    meaningless once a draft is complete.

Parity & caveats (flagged per the spec)
----------------------------------------
  - The per-cell z is read from the SAME `Store.block/cell` the live `score_draft`
    uses, so feature values match the engine exactly at the cell level.
  - Aggregation divides the sum of KNOWN pairings by the FULL 5v5 count (unknown
    pairings contribute 0). For a complete, fully-covered draft this equals the
    mean (so training rows are unchanged); for a partial draft it SHRINKS the
    feature toward 0 in proportion to how much is still unknown — so early-pick
    win% sits near 50% and grows confident as the board fills in, instead of
    over-reading a lone pick on the wrong (5v5-averaged) scale.
  - Matchup z is column-normalized per block, so it is only *approximately*
    antisymmetric across the two role-order blocks; the two perspectives of one
    game are near-negatives, not exact. The fitted intercept should be ~0 anyway.
  - Matchup/synergy z carry no rank dimension (the `cells` table has none); only
    `champ_strength` (win_rate) is rank-specific.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .store import Store

FEATURE_NAMES = ["lane_z", "counter_z", "synergy_z", "champ_strength"]
ROLES = config.ROLES  # ["TOP", "JUNGLE", "MID", "ADC", "SUP"]

# Full 5v5 pairing counts. Features are summed over KNOWN pairings then divided
# by these fixed denominators, so a partial draft shrinks toward neutral (0).
N_LANES = 5            # head-to-head lanes
N_CROSS = 20           # 5 allies x 5 enemies - 5 same-role
N_SYNERGY_PAIRS = 10   # C(5, 2) intra-team pairs
N_TEAM = 5             # champions per team


@dataclass
class DraftFeatures:
    values: dict[str, float]                 # name -> value (FEATURE_NAMES)
    coverage: dict[str, int] = field(default_factory=dict)   # pairings actually used
    complete: bool = True                    # all 10 slots filled & every cell found

    def vector(self) -> list[float]:
        return [self.values[k] for k in FEATURE_NAMES]


def _team_synergy(store: Store, team: dict[str, str]) -> tuple[float, int]:
    """(sum of intra-team synergy z over known pairs, n pairs). Mirrors
    evaluate.py; returned as a sum so the caller divides by the full pair count."""
    roles = [r for r in ROLES if team.get(r)]
    total, n = 0.0, 0
    for i, ra in enumerate(roles):
        for rb in roles[i + 1:]:
            cell = store.cell("synergy", ra, rb, team[ra], team[rb])
            if cell is not None:
                total += cell[1]  # z
                n += 1
    return total, n


def _team_strength(store: Store, team: dict[str, str], rank: str) -> tuple[float, int]:
    """(sum of win-rate EDGE over an even 50% baseline, n known champions).

    Centering on 0.5 means an unknown/absent champion contributes 0 (neutral),
    so the champ_strength DIFFERENCE stays valid mid-draft when a side is empty.
    Returned as a sum; the caller divides by the full team size."""
    total, n = 0.0, 0
    for r in ROLES:
        champ = team.get(r)
        if not champ:
            continue
        wr = store.win_rate(rank, r, champ)
        if wr is not None:
            total += wr - 0.5
            n += 1
    return total, n


def draft_features(store: Store, allies: dict[str, str], enemies: dict[str, str],
                   rank: str | None = None) -> DraftFeatures:
    """Turn one team's draft view into the model feature vector.

    allies / enemies are {engine-role: champion}; missing roles are simply
    omitted (never penalized). `rank` selects the win_rate bracket for
    champ_strength (defaults to config.DEFAULT_RANK)."""
    rank = rank or config.DEFAULT_RANK

    # lane_z: same-role head-to-head matchup z (ally vs enemy).
    lane_vals: list[float] = []
    for r in ROLES:
        a, e = allies.get(r), enemies.get(r)
        if a and e:
            cell = store.cell("matchup", r, r, a, e)
            if cell is not None:
                lane_vals.append(cell[1])  # z

    # counter_z: cross-role matchup z (ally role != enemy role).
    counter_vals: list[float] = []
    for ra in ROLES:
        a = allies.get(ra)
        if not a:
            continue
        for rb in ROLES:
            if ra == rb:
                continue
            e = enemies.get(rb)
            if not e:
                continue
            cell = store.cell("matchup", ra, rb, a, e)
            if cell is not None:
                counter_vals.append(cell[1])  # z

    syn_a, n_syn_a = _team_synergy(store, allies)
    syn_e, n_syn_e = _team_synergy(store, enemies)
    str_a, n_str_a = _team_strength(store, allies, rank)
    str_e, n_str_e = _team_strength(store, enemies, rank)

    # sum over known pairings / FULL 5v5 count -> shrinks toward 0 when partial
    values = {
        "lane_z": sum(lane_vals) / N_LANES,
        "counter_z": sum(counter_vals) / N_CROSS,
        "synergy_z": (syn_a - syn_e) / N_SYNERGY_PAIRS,
        "champ_strength": (str_a - str_e) / N_TEAM,
    }
    coverage = {
        "lane_z": len(lane_vals),
        "counter_z": len(counter_vals),
        "synergy_z": n_syn_a + n_syn_e,
        "champ_strength": n_str_a + n_str_e,
    }
    complete = (
        len(allies) == 5 and len(enemies) == 5
        and len(lane_vals) == 5 and len(counter_vals) == 20
        and n_syn_a == 10 and n_syn_e == 10
        and n_str_a == 5 and n_str_e == 5
    )
    return DraftFeatures(values, coverage, complete)
