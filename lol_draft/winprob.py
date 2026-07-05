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
from .features import (N_CROSS, N_LANES, N_SYNERGY_PAIRS, N_TEAM,
                       DraftFeatures, draft_features)
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


class ZCellNoise:
    """Cell-consistent draws of matchup/synergy z-cell uncertainty (WS3).

    Published z cells are ALREADY shrunk toward the mean, so treating each
    published value as the posterior mean of a normal-normal EB model with
    z_true ~ N(0, tau^2 = 1) and per-cell observation noise s^2 leaves the
    remaining posterior variance

        v_cell = s^2 / (s^2 + 1)      (< 1 by construction)

    NOT `z_pub ± SE` — that would double-count uncertainty on well-sampled
    cells. Per-cell sample size is not published; the proxy is
    N̂ = G_rolepair · PR_A · PR_B (per-role playrates + role game totals, with
    G_rolepair the mean of the two roles' totals) and s^2 = c / N̂ with the
    single global constant `c` from data/models/zcell_noise.json (snapshot-
    calibrated when two archives exist, heuristic fallback otherwise). N̂ -> 0
    degrades to v -> 1: an unrepresented pairing gets full population noise.

    Draws are deterministically keyed on the CELL identity (mode + roles +
    champions) per replicate index, and cached — a cell shared between two
    candidate evaluations in the same replicate gets the identical draw and
    cancels in head-to-head comparisons, same discipline as the
    champ_strength resampling."""

    def __init__(self, store: Store, rank: str, n: int, c: float,
                 seed: int = FV_SEED_DEFAULT):
        self.store, self.rank, self.n, self.c, self.seed = store, rank, n, c, seed
        self._pr: dict[str, dict[str, float]] = {}
        self._g: dict[str, int] = {}
        self._draws: dict[tuple, list[float]] = {}

    def _role(self, role: str):
        if role not in self._pr:
            self._pr[role] = self.store.pick_rates(self.rank, role)
            self._g[role] = self.store.role_games(self.rank, role)

    def v_cell(self, role_a: str, role_b: str, champ_a: str, champ_b: str) -> float:
        self._role(role_a)
        self._role(role_b)
        n_hat = (0.5 * (self._g[role_a] + self._g[role_b])
                 * self._pr[role_a].get(champ_a, 0.0)
                 * self._pr[role_b].get(champ_b, 0.0))
        if n_hat <= 0.0:
            return 1.0                       # never seen together: full prior sd
        s2 = self.c / n_hat
        return s2 / (s2 + 1.0)

    def draws(self, mode: str, role_a: str, role_b: str,
              champ_a: str, champ_b: str) -> list[float]:
        key = (mode, role_a, role_b, champ_a, champ_b)
        cached = self._draws.get(key)
        if cached is not None:
            return cached
        sd = math.sqrt(self.v_cell(role_a, role_b, champ_a, champ_b))
        normals = _stable_normals(self.n, self.seed, "zcell", *key)
        out = [sd * x for x in normals]
        self._draws[key] = out
        return out

    def sum_series(self, cells: list[tuple]) -> list[float]:
        """Σ sign·ε over the given (mode, ra, rb, a, b, sign) cells, per member."""
        total = [0.0] * self.n
        for mode, ra, rb, a, b, sign in cells:
            d = self.draws(mode, ra, rb, a, b)
            if sign == 1.0:
                for i in range(self.n):
                    total[i] += d[i]
            else:
                for i in range(self.n):
                    total[i] += sign * d[i]
        return total


class PosteriorNoise:
    """Champion-consistent draws of each champion's edge from its own-data EB
    posterior (WS2): edge_b = clamp(m - 0.5 + sqrt(v) * z_b), the Normal(m, √v)
    approximation of the Beta posterior. Same interface, keying discipline and
    cache as WinRateNoise, so it's a drop-in for the uncertainty path when the
    model was trained on own-data strength. Champion-level: the draw key ignores
    role, matching the feature's keying."""

    def __init__(self, strength, n: int, seed: int = FV_SEED_DEFAULT):
        self.strength, self.n, self.seed = strength, n, seed
        self._cache: dict[str, list[float]] = {}

    def edge_draws(self, role: str, champ: str) -> list[float]:
        cached = self._cache.get(champ)
        if cached is not None:
            return cached
        edge = self.strength.edge(champ)
        if edge is None:
            draws = [0.0] * self.n           # unknown champ contributes nothing
        else:
            sd = self.strength.sd(champ)
            if sd == 0.0:
                draws = [edge] * self.n
            else:
                normals = _stable_normals(self.n, self.seed, "own_data", champ)
                draws = [min(0.5, max(-0.5, edge + sd * z)) for z in normals]
        self._cache[champ] = draws
        return draws


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


def _enumerate_candidates(store: Store, state: DraftState, rank: str,
                          strength=None, collect_cells: bool = False
                          ) -> tuple[list[tuple[str, DraftFeatures]], list[str]]:
    """Shared candidate enumeration + feature build for the point and uncertainty
    rankers. Returns ([(champion, features)], warnings). Availability rules (bans,
    picked, pool) mirror `scoring.score_draft` exactly. `strength` (WS2) selects
    champ_strength's source — pass the ChampStrength matching the model's meta."""
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
        out.append((c, draft_features(store, my_team, enemies, rank,
                                      strength=strength,
                                      collect_cells=collect_cells)))
    return out, warnings


def rank_candidates(store: Store, state: DraftState, model: WinProbModel,
                    *, rank: str | None = None, strength=None
                    ) -> tuple[list[CandidateWinProb], list[str]]:
    """Return (candidates ranked by P(win) desc, warnings)."""
    rank = rank or config.DEFAULT_RANK
    feats, warnings = _enumerate_candidates(store, state, rank, strength)
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
    strength=None, z_cell_c: float | None = None,
    _vectors_out: list | None = None,
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
    n = len(ensemble.members)
    z_noise = (ZCellNoise(store, rank, n, z_cell_c, fv_seed)
               if (propagate_feature_uncertainty and z_cell_c is not None
                   and z_cell_c > 0) else None)
    feats, warnings = _enumerate_candidates(store, state, rank, strength,
                                            collect_cells=z_noise is not None)

    my_role = state.my_role
    allies = {r: c for r, c in state.allies.items() if r != my_role}
    enemies = dict(state.enemies)
    # Feature-value noise source follows the feature source: own-data models
    # draw from the EB Beta posterior (champion-keyed), machineloling models
    # from the published win rate's binomial SE. Same interface either way.
    noise = None
    if propagate_feature_uncertainty:
        noise = (PosteriorNoise(strength, n, fv_seed) if strength is not None
                 else WinRateNoise(store, rank, n, fv_seed))

    # z-cell noise (WS3): the fixed picks' cells are shared by every candidate,
    # so their signed draw-sum is computed ONCE; each candidate adds only the
    # cells its own champion introduces. Same shared-base pattern (and the same
    # cancellation guarantee) as the champ_strength edges below.
    Z_DENOM = {"lane_z": N_LANES, "counter_z": N_CROSS, "synergy_z": N_SYNERGY_PAIRS}
    z_base: dict[str, list[float]] = {}
    z_base_keys: dict[str, set] = {}
    if z_noise is not None:
        base_f = draft_features(store, allies, enemies, rank, collect_cells=True)
        for feat in Z_DENOM:
            z_base[feat] = z_noise.sum_series(base_f.cells[feat])
            z_base_keys[feat] = {c[:5] for c in base_f.cells[feat]}

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
        if z_noise is not None:
            for feat, denom in Z_DENOM.items():
                own = [cl for cl in f.cells[feat] if cl[:5] not in z_base_keys[feat]]
                zb = z_base[feat]
                if own:
                    zo = z_noise.sum_series(own)
                    series[feat] = [f.values[feat] + (zb[b] + zo[b]) / denom
                                    for b in range(n)]
                else:
                    series[feat] = [f.values[feat] + zb[b] / denom for b in range(n)]
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
    if _vectors_out is not None:  # benchmark hook: per-candidate member vectors,
        _vectors_out.extend(      # ranked order (for adjacent-pair tie stats)
            (results[i].champion, vectors[i]) for i in range(len(results)))

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
