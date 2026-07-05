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

import hashlib
import math
import random
from dataclasses import dataclass, field

from . import config
from .features import N_TEAM, DraftFeatures, draft_features
from .model import WinProbEnsemble, WinProbModel, _percentile
from .scoring import DraftState
from .store import Store

# Default distinguishability bar: pick A is called meaningfully better than B
# only if A beats B in >= this fraction of bootstrap resamples. Below it, the two
# are "too close to call". 0.85 ~ a one-sided 85% confidence the ranking is real;
# tune via rank_candidates_uncertain(..., tie_threshold=...).
TIE_THRESHOLD_DEFAULT = 0.85

# Seed for the feature-value (champ_strength) perturbation draws. Separate from
# the bootstrap-fit seed; fixed so the propagated intervals are reproducible.
FV_SEED_DEFAULT = 20260601


def _stable_normals(n: int, *key) -> list[float]:
    """n reproducible standard normals, deterministically keyed by `key`.

    Uses a hashlib digest (NOT Python's hash(), which is salted across processes
    for strings) to seed a per-key RNG, so champion c's b-th draw is identical in
    training-feature space and at predict time, and identical across runs/processes
    — which is what keeps the paired distinguishability test honest (a shared champ
    gets the SAME win-rate error in both drafts, so it cancels)."""
    digest = hashlib.sha256("|".join(str(p) for p in key).encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    return [rng.gauss(0.0, 1.0) for _ in range(n)]


class WinRateNoise:
    """Champion-consistent binomial draws of each champion's win-rate edge.

    win_rate is a proportion estimated from a known number of games, so it carries
    a binomial standard error SE = sqrt(wr*(1-wr)/games). This draws, for every
    champion, an N-length series of perturbed (win_rate - 0.5) values — the exact
    quantity champ_strength sums — consistent per champion (cached + deterministically
    keyed) so the same champ perturbs identically wherever it appears in a draft."""

    def __init__(self, store: Store, rank: str, n: int, seed: int = FV_SEED_DEFAULT):
        self.store, self.rank, self.n, self.seed = store, rank, n, seed
        self._cache: dict[tuple[str, str], list[float]] = {}

    def edge_draws(self, role: str, champ: str) -> list[float]:
        """N draws of (win_rate - 0.5) for this champion, perturbed by its binomial
        SE. Unknown champion -> zeros (contributes nothing, matching features.py).
        SE=0 (no games) -> the point edge repeated (no spurious noise)."""
        key = (role, champ)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        wg = self.store.win_rate_games(self.rank, role, champ)
        if wg is None:
            draws = [0.0] * self.n
        else:
            wr, games = wg
            se = math.sqrt(wr * (1.0 - wr) / games) if games > 0 else 0.0
            if se == 0.0:
                draws = [wr - 0.5] * self.n
            else:
                normals = _stable_normals(self.n, self.seed, role, champ)
                # clamp the perturbed win_rate to (0,1) before centering on 0.5
                draws = [min(0.5, max(-0.5, (wr + se * z) - 0.5)) for z in normals]
        self._cache[key] = draws
        return draws


def champ_strength_series(noise: WinRateNoise, allies: dict[str, str],
                          enemies: dict[str, str]) -> list[float]:
    """Per-member champ_strength = (Σ ally edge − Σ enemy edge) / N_TEAM, with each
    champion's win-rate edge resampled from its binomial SE. Mean over members
    equals the point champ_strength; the spread is the feature's sampling error."""
    n = noise.n
    ally = [0.0] * n
    enemy = [0.0] * n
    for role, ch in allies.items():
        d = noise.edge_draws(role, ch)
        for b in range(n):
            ally[b] += d[b]
    for role, ch in enemies.items():
        d = noise.edge_draws(role, ch)
        for b in range(n):
            enemy[b] += d[b]
    return [(ally[b] - enemy[b]) / N_TEAM for b in range(n)]


@dataclass
class CandidateWinProb:
    champion: str
    win_prob: float                       # calibrated P(win) if we pick this champ
    features: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)  # logit push per feature
    coverage: dict[str, int] = field(default_factory=dict)
    complete: bool = False                # was every pairing's cell present


@dataclass
class CandidateUncertainty:
    """A candidate with bootstrap error bars instead of a lone point estimate."""
    champion: str
    median: float                         # median P(win) across the ensemble
    p_lo: float                           # 5th percentile (interval floor)
    p_hi: float                           # 95th percentile (interval ceiling)
    std: float                            # sd of the win-prob distribution
    point: float                          # deployed single-model P(win), for reference
    features: dict[str, float] = field(default_factory=dict)
    coverage: dict[str, int] = field(default_factory=dict)
    complete: bool = False
    tied_with_top: bool = False           # not distinguishable from the top pick
    prob_top_better: float | None = None  # P(top pick > this) across resamples; None for the top pick
    lo_pct: float = 5.0
    hi_pct: float = 95.0


def _enumerate_candidates(store: Store, state: DraftState, rank: str
                          ) -> tuple[list[tuple[str, DraftFeatures]], list[str]]:
    """Shared candidate enumeration + feature build for the point and uncertainty
    rankers. Returns ([(champion, features)], warnings). Availability rules (bans,
    picked, pool) mirror `scoring.score_draft` exactly."""
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

    out: list[tuple[str, DraftFeatures]] = []
    for c in candidates:
        my_team = {**allies, my_role: c}
        out.append((c, draft_features(store, my_team, enemies, rank)))
    return out, warnings


def rank_candidates(store: Store, state: DraftState, model: WinProbModel,
                    *, rank: str | None = None) -> tuple[list[CandidateWinProb], list[str]]:
    """Return (candidates ranked by P(win) desc, warnings)."""
    rank = rank or config.DEFAULT_RANK
    feats, warnings = _enumerate_candidates(store, state, rank)
    results = [
        CandidateWinProb(
            champion=c,
            win_prob=model.predict_proba(f.values),
            features=f.values,
            contributions=model.contributions(f.values),
            coverage=f.coverage,
            complete=f.complete,
        )
        for c, f in feats
    ]
    results.sort(key=lambda r: r.win_prob, reverse=True)
    return results, warnings


def rank_candidates_uncertain(
    store: Store, state: DraftState, ensemble: WinProbEnsemble,
    *, rank: str | None = None, point_model: WinProbModel | None = None,
    tie_threshold: float = TIE_THRESHOLD_DEFAULT,
    propagate_feature_uncertainty: bool = True, fv_seed: int = FV_SEED_DEFAULT,
) -> tuple[list[CandidateUncertainty], list[str]]:
    """Rank candidates by MEDIAN win probability and attach an error bar to each
    (90% interval + sd), then flag every pick statistically tied with the top pick.

    TWO sources of uncertainty are combined, member by member:
      1. COEFFICIENT noise — the bootstrap-refit ensemble (which games we observed).
      2. FEATURE-VALUE noise — champ_strength is built from champion win_rates, each
         a binomial proportion with a finite `games` count, so it has a real standard
         error √(wr(1-wr)/games). When `propagate_feature_uncertainty` is on, member
         b scores a champ_strength resampled from that SE (champion-consistent), so
         the interval reflects BOTH how noisy the weights are AND how noisy the inputs
         are. This is the dominant term and roughly doubles the honest interval.
         (Per-cell N for the matchup/synergy z's isn't published, so those stay at
         their point values here — a separate, heuristic step.) Prediction-time only:
         we don't re-fit on perturbed features, so coefficient attenuation (regression
         dilution) is not modelled.

    Distinguishability is PAIRED: for the top pick T and each other candidate C, we
    count the fraction of members in which T's P(win) exceeds C's, using the SAME
    member's coefficients AND the same per-champion win-rate draws — so shared champs
    cancel and only the genuine T-vs-C difference (plus its uncertainty) drives the
    verdict. Below `tie_threshold` -> 'too close to call' (`tied_with_top`).
    """
    rank = rank or config.DEFAULT_RANK
    feats, warnings = _enumerate_candidates(store, state, rank)
    n = len(ensemble.members)

    my_role = state.my_role
    allies = {r: c for r, c in state.allies.items() if r != my_role}
    enemies = dict(state.enemies)
    noise = WinRateNoise(store, rank, n, fv_seed) if propagate_feature_uncertainty else None

    # The allies/enemies are shared across every candidate in this role, so their
    # win-rate-edge sum is computed ONCE here; each candidate just adds its own
    # champ's edge draws. (champ_strength = (Σ ally − Σ enemy + candidate) / N_TEAM.)
    base = [0.0] * n
    if noise is not None:
        for role, ch in allies.items():
            d = noise.edge_draws(role, ch)
            for b in range(n):
                base[b] += d[b]
        for role, ch in enemies.items():
            d = noise.edge_draws(role, ch)
            for b in range(n):
                base[b] -= d[b]

    results: list[CandidateUncertainty] = []
    vectors: list[list[float]] = []           # per-candidate P(win) across members
    for c, f in feats:
        series: dict[str, float | list[float]] = dict(f.values)
        if noise is not None:
            cd = noise.edge_draws(my_role, c)
            series["champ_strength"] = [(base[b] + cd[b]) / N_TEAM for b in range(n)]
        probs = ensemble.distribution(series)
        vectors.append(probs)
        srt = sorted(probs)
        mean = sum(probs) / n
        var = sum((p - mean) ** 2 for p in probs) / n
        results.append(CandidateUncertainty(
            champion=c,
            median=_percentile(srt, 50.0), p_lo=_percentile(srt, 5.0),
            p_hi=_percentile(srt, 95.0), std=math.sqrt(var),
            point=(point_model.predict_proba(f.values) if point_model else _percentile(srt, 50.0)),
            features=f.values, coverage=f.coverage, complete=f.complete,
            lo_pct=5.0, hi_pct=95.0,
        ))

    order = sorted(range(len(results)), key=lambda i: results[i].median, reverse=True)
    results = [results[i] for i in order]
    vectors = [vectors[i] for i in order]

    if results:
        top_vec = vectors[0]
        results[0].prob_top_better = None
        results[0].tied_with_top = False
        for i in range(1, len(results)):
            v = vectors[i]
            wins = 0.0
            for b in range(n):
                if top_vec[b] > v[b]:
                    wins += 1.0
                elif top_vec[b] == v[b]:
                    wins += 0.5
            p = wins / n
            results[i].prob_top_better = p
            results[i].tied_with_top = p < tie_threshold
    return results, warnings


def distinguish(ensemble: WinProbEnsemble, feats_a: dict[str, float],
                feats_b: dict[str, float], *,
                tie_threshold: float = TIE_THRESHOLD_DEFAULT) -> dict:
    """Head-to-head distinguishability of two drafts. Returns the paired
    P(A>B), the median win probs, and a verdict in {'A', 'B', 'too close to
    call'} under `tie_threshold`."""
    p_a_better = ensemble.prob_a_better(feats_a, feats_b)
    if p_a_better >= tie_threshold:
        verdict = "A"
    elif (1.0 - p_a_better) >= tie_threshold:
        verdict = "B"
    else:
        verdict = "too close to call"
    return {
        "p_a_better": p_a_better,
        "median_a": ensemble.summarize(feats_a)["median"],
        "median_b": ensemble.summarize(feats_b)["median"],
        "tie_threshold": tie_threshold,
        "verdict": verdict,
    }
