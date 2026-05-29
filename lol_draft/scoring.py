"""EV scoring engine.

Given a (partial) draft state and the role we are picking for, score every
available candidate by combining four z-score components, mirroring the
Champion Pool Designer formula:

    Total = w_in · InLane + w_out · OutOfLane + w_syn · Synergy + w_blind · Blind

- InLane     : candidate's matchup z vs the direct-lane enemy (same-role block).
- OutOfLane  : aggregate of candidate's matchup z vs every OTHER enemy
               (cross-role blocks).
- Synergy    : aggregate of candidate's synergy z with every known ally.
- Blind      : playrate-weighted mean of the candidate's in-lane matchup z
               across the whole field of same-role opponents (how safe to
               blind-pick). Derived purely from validated z-data; labelled
               approximate vs the site's internal metric.

Unknown slots are simply omitted — a partial draft never penalizes a
candidate for missing information, and every candidate is judged against the
same known state, so comparisons stay fair.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .store import Store


@dataclass
class Contribution:
    name: str          # opponent / ally champion
    role: str
    z: float
    pp: float


@dataclass
class Component:
    value: float | None                       # aggregated z (None if no data)
    weight: float
    contributions: list[Contribution] = field(default_factory=list)
    detail: str = ""                          # free-form note (e.g. blindability raw)

    @property
    def weighted(self) -> float:
        return 0.0 if self.value is None else self.weight * self.value


@dataclass
class CandidateScore:
    champion: str
    total: float
    components: dict[str, Component]


@dataclass
class DraftState:
    my_role: str
    enemies: dict[str, str] = field(default_factory=dict)   # role -> champ
    allies: dict[str, str] = field(default_factory=dict)    # role -> champ
    bans: list[str] = field(default_factory=list)
    pool: list[str] | None = None


def _aggregate(values: list[float], method: str, top_n: int) -> float | None:
    if not values:
        return None
    if method == "sum":
        return float(sum(values))
    if method == "topn":
        chosen = sorted(values, reverse=True)[: max(1, top_n)]
        return float(sum(chosen) / len(chosen))
    return float(sum(values) / len(values))  # mean (default)


def score_draft(
    store: Store,
    state: DraftState,
    *,
    rank: str | None = None,
    weights: dict[str, float] | None = None,
    agg: str | None = None,
    top_n: int | None = None,
) -> tuple[list[CandidateScore], list[str]]:
    """Return (ranked candidate scores desc, warnings)."""
    settings = store.settings()
    rank = rank or settings.get("default_rank", config.DEFAULT_RANK)
    weights = weights or store.weights()
    agg = agg or settings.get("agg", config.DEFAULT_AGG)
    top_n = top_n if top_n is not None else int(settings.get("top_n", config.DEFAULT_TOP_N))

    my_role = state.my_role
    warnings: list[str] = []
    valid_champs = store.all_champions()

    # Validate provided champions.
    for role, champ in {**state.enemies, **state.allies}.items():
        if champ not in valid_champs:
            warnings.append(f"Unknown champion {champ!r} (role {role}); ignored where unmatched.")
    # Allies can't occupy my own role.
    allies = {r: c for r, c in state.allies.items() if r != my_role}
    if any(r == my_role for r in state.allies):
        warnings.append(f"Ally listed in my own role ({my_role}); ignored.")

    # --- pre-load the blocks we need (one query each) ---
    inlane = store.block("matchup", my_role, my_role)          # in-lane + blindability
    enemy_blocks = {
        r: store.block("matchup", my_role, r)
        for r in state.enemies if r != my_role
    }
    ally_blocks = {r: store.block("synergy", my_role, r) for r in allies}

    # --- blindability: the site's own blind-pick field-safety metric
    # (oracle-derived `aggregate`), standardized across the whole role so it
    # sits on the same unit-z scale as the other three z-components. Falls back
    # to absent (no contribution) if the blindability table wasn't generated. ---
    blind_tbl = store.blindability(rank, my_role)
    if not blind_tbl:
        warnings.append(
            "Blindability table missing (run `node tools/gen_blindability.mjs` "
            "then rebuild); blindability term omitted."
        )
    raw_blind = {c: d["aggregate"] for c, d in blind_tbl.items() if d.get("aggregate") is not None}
    if raw_blind:
        vals = list(raw_blind.values())
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        blind_z = {k: (v - mu) / sd for k, v in raw_blind.items()}
    else:
        blind_z = {}

    # --- candidate set ---
    candidates = store.role_champions(my_role)
    unavailable = set(state.bans) | set(state.enemies.values()) | set(allies.values())
    candidates = [c for c in candidates if c not in unavailable]
    if state.pool is not None:
        poolset = set(state.pool)
        missing = poolset - set(store.role_champions(my_role))
        for m in sorted(missing):
            if m in valid_champs:
                warnings.append(f"Pool champion {m!r} is not playable in {my_role}; skipped.")
            else:
                warnings.append(f"Pool champion {m!r} is unknown; skipped.")
        candidates = [c for c in candidates if c in poolset]

    direct_enemy = state.enemies.get(my_role)

    results: list[CandidateScore] = []
    for c in candidates:
        comps: dict[str, Component] = {}

        # in-lane (single direct opponent)
        in_contribs: list[Contribution] = []
        in_val = None
        if direct_enemy is not None:
            cell = inlane.get(c, {}).get(direct_enemy)
            if cell is not None:
                pp, z = cell
                in_contribs.append(Contribution(direct_enemy, my_role, z, pp))
                in_val = z
        comps["in_lane"] = Component(in_val, weights.get("in_lane", 0.0), in_contribs)

        # out-of-lane (other enemies)
        out_vals, out_contribs = [], []
        for r, e in state.enemies.items():
            if r == my_role:
                continue
            cell = enemy_blocks.get(r, {}).get(c, {}).get(e)
            if cell is not None:
                pp, z = cell
                out_vals.append(z)
                out_contribs.append(Contribution(e, r, z, pp))
        comps["out_of_lane"] = Component(
            _aggregate(out_vals, agg, top_n), weights.get("out_of_lane", 0.0), out_contribs
        )

        # synergy (allies)
        syn_vals, syn_contribs = [], []
        for r, a in allies.items():
            cell = ally_blocks.get(r, {}).get(c, {}).get(a)
            if cell is not None:
                pp, z = cell
                syn_vals.append(z)
                syn_contribs.append(Contribution(a, r, z, pp))
        comps["synergy"] = Component(
            _aggregate(syn_vals, agg, top_n), weights.get("synergy", 0.0), syn_contribs
        )

        # blindability (standardized oracle field-safety z; see precompute above)
        bdetail = ""
        if c in blind_tbl:
            d = blind_tbl[c]
            bdetail = (f"agg={d['aggregate']:.3f} "
                       f"(lane={d['lane_matchup']:.2f} "
                       f"out={d['out_of_lane_matchup']:.2f} "
                       f"syn={d['out_of_lane_synergy']:.2f})")
        comps["blindability"] = Component(
            blind_z.get(c), weights.get("blindability", 0.0), detail=bdetail
        )

        total = sum(comp.weighted for comp in comps.values())
        results.append(CandidateScore(c, total, comps))

    results.sort(key=lambda r: r.total, reverse=True)
    return results, warnings
