"""Own-data, patch-stamped champion strength (WS2).

Replaces the machineloling-sourced `champ_strength` feature input with
per-champion, per-patch win rates computed from our own Riot Match-V5
collection (data/games/*.jsonl), smoothed with an empirical-Bayes random-walk
(Beta-Binomial) prior:

    prior at t=1 : Beta(0.5*KAPPA0, 0.5*KAPPA0)          weak pull to 50%
    prior at t>1 : Beta(m_{t-1}*KAPPA, (1-m_{t-1})*KAPPA)  carry last patch
    posterior    : alpha' = alpha + wins_t, beta' = beta + losses_t

so a champion with ~40 games in a patch mostly inherits last patch's estimate
while one with ~2000 games mostly stands on its own. This fixes the
feature-label meta-mismatch: training rows are featurized with the posterior
AS OF THEIR PATCH (leave-one-match-out — see `loo_view`), serving uses the
latest patch's full posterior.

KEYING (decided at Checkpoint 0/1): champion-level — role and rank collapsed.
Per-patch samples are the binding constraint and role-splitting would starve
the estimator; collapsing rank makes the feature label-aligned (the strength
estimates come from the same mixed-tier population as the training labels).
The machineloling path (rank+role win rates) remains the default; a model only
uses this artifact when its meta says `champ_strength_source: own_data_eb`,
which is what keeps train and serve from ever disagreeing about the feature.

Artifact: data/models/champ_strength.json (committed — the serve path needs it
and data/games/ is not in git). Rebuild:

    python -m lol_draft.champstats build [--data ...] [--kappa0 50] [--kappa 200]
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .model import models_dir

KAPPA0 = 50.0    # pseudo-games anchoring the very first patch toward 50%
KAPPA = 200.0    # pseudo-games carrying the previous patch's mean forward

ARTIFACT_VERSION = 1


def default_strength_path() -> Path:
    return models_dir() / "champ_strength.json"


def _patch_key(patch: str) -> tuple[int, int]:
    """Chronological sort key: '16.9' < '16.10' (string sort gets this wrong)."""
    parts = patch.split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return (0, 0)


def aggregate_rows(rows: list[dict]) -> dict[str, dict[str, list[int]]]:
    """{champion: {patch: [wins, games]}} from labeled rows.

    Each match yields TWO rows (both perspectives); every champion in the match
    appears exactly once as an ALLY across the pair, so aggregating allies only
    counts each participant-game exactly once."""
    agg: dict[str, dict[str, list[int]]] = {}
    for r in rows:
        patch, win = r["patch"], int(r["win"])
        for champ in r["allies"].values():
            cell = agg.setdefault(champ, {}).setdefault(patch, [0, 0])
            cell[0] += win
            cell[1] += 1
    return agg


def fit_random_walk(agg: dict[str, dict[str, list[int]]], patches: list[str],
                    *, kappa0: float = KAPPA0, kappa: float = KAPPA) -> dict:
    """Run the Beta-Binomial random walk for every champion over `patches`
    (chronological). Returns {champion: {patch: {prior_a, prior_b, wins, games,
    mean, var}}} — the prior params are stored so training can form the
    leave-one-match-out posterior without refitting the chain."""
    out: dict[str, dict[str, dict]] = {}
    for champ, by_patch in agg.items():
        chain: dict[str, dict] = {}
        m_prev: float | None = None
        for patch in patches:
            if m_prev is None:
                a0, b0 = 0.5 * kappa0, 0.5 * kappa0
            else:
                a0, b0 = m_prev * kappa, (1.0 - m_prev) * kappa
            wins, games = by_patch.get(patch, (0, 0))
            a, b = a0 + wins, b0 + (games - wins)
            mean = a / (a + b)
            var = (a * b) / ((a + b) ** 2 * (a + b + 1.0))
            chain[patch] = {"prior_a": a0, "prior_b": b0, "wins": wins,
                            "games": games, "mean": mean, "var": var}
            m_prev = mean
        out[champ] = chain
    return out


def load_rows(path: Path) -> list[dict]:
    """Labeled JSONL rows (same reader as train.py — duplicated here so the
    artifact build doesn't drag sklearn in via the train module)."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_artifact(data_path: Path, *, kappa0: float = KAPPA0,
                   kappa: float = KAPPA) -> dict:
    rows = load_rows(data_path)
    agg = aggregate_rows(rows)
    patches = sorted({r["patch"] for r in rows}, key=_patch_key)
    chains = fit_random_walk(agg, patches, kappa0=kappa0, kappa=kappa)
    latest = patches[-1] if patches else None
    return {
        "version": ARTIFACT_VERSION,
        "meta": {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "data": str(data_path),
            "n_rows": len(rows),
            "n_matches": len({r["matchId"] for r in rows}),
            "n_champions": len(chains),
            "patches": patches,
            "latest_patch": latest,
            "kappa0": kappa0,
            "kappa": kappa,
            "keying": "champion (role+rank collapsed)",
        },
        "champions": chains,
    }


class ChampStrength:
    """Read access to the artifact: serve-path edges + training LOO views.

    `edge(champ)` is the latest patch's posterior mean − 0.5 — the exact
    quantity features.py sums (same centering/scale as the machineloling
    win_rate path it replaces). Unknown champion -> None (skipped, matching the
    'no data' behavior of the current feature)."""

    def __init__(self, artifact: dict):
        self.meta = artifact.get("meta", {})
        self.champions = artifact.get("champions", {})
        self.patches: list[str] = self.meta.get("patches", [])
        self.latest_patch: str | None = self.meta.get("latest_patch")

    @classmethod
    def load(cls, path: Path | None = None) -> "ChampStrength":
        path = path or default_strength_path()
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def _cell(self, champ: str, patch: str | None) -> dict | None:
        chain = self.champions.get(champ)
        if not chain or patch is None:
            return None
        return chain.get(patch)

    # --- serve path (latest posterior) ---
    def edge(self, champ: str) -> float | None:
        cell = self._cell(champ, self.latest_patch)
        return None if cell is None else cell["mean"] - 0.5

    def sd(self, champ: str) -> float:
        cell = self._cell(champ, self.latest_patch)
        return 0.0 if cell is None else math.sqrt(max(cell["var"], 0.0))

    # --- training path (leave-one-match-out, as of the game's patch) ---
    def loo_view(self, patch: str, win_by_champ: dict[str, int]) -> "_LooView":
        """A per-row strength view: `edge(champ)` is the posterior at `patch`
        with THIS match's own contribution (1 game, its win/loss) removed —
        the label leak GroupKFold cannot catch. Champions not in this match
        get the plain posterior."""
        return _LooView(self, patch, win_by_champ)


class _LooView:
    def __init__(self, strength: ChampStrength, patch: str,
                 win_by_champ: dict[str, int]):
        self._s = strength
        self._patch = patch
        self._w = win_by_champ

    def edge(self, champ: str) -> float | None:
        cell = self._s._cell(champ, self._patch)
        if cell is None:
            return None
        w = self._w.get(champ)
        if w is None:  # champ not in this match: plain posterior
            return cell["mean"] - 0.5
        a = cell["prior_a"] + cell["wins"] - w
        b = cell["prior_b"] + (cell["games"] - cell["wins"]) - (1 - w)
        return a / (a + b) - 0.5


def strength_for_meta(meta: dict, path: Path | None = None,
                      *, warn: bool = True) -> ChampStrength | None:
    """The ChampStrength a model's meta calls for, or None.

    THE coupling point between model artifact and feature definition: a model
    trained with own-data strength says so in its meta, and every scoring path
    (server, CLI) resolves the feature source from that — never from a flag
    that could drift out of sync with the coefficients."""
    if meta.get("champ_strength_source") != "own_data_eb":
        return None
    try:
        return ChampStrength.load(path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        if warn:
            print("  ! model expects own-data champ_strength but "
                  f"{path or default_strength_path()} is missing/unreadable; "
                  "falling back to machineloling win rates (values will be "
                  "slightly miscalibrated — rebuild with "
                  "`python -m lol_draft.champstats build`)")
        return None


# --- diagnostics (Checkpoint 2 deliverables) ---------------------------------
def diagnostics(artifact: dict) -> dict:
    """Coverage + shrinkage summary: games per patch, distribution of
    per-champion per-patch games, median share of posterior weight coming from
    the prior (over cells with games > 0)."""
    champs = artifact["champions"]
    patches = artifact["meta"]["patches"]
    games_per_patch = {p: 0 for p in patches}
    cell_games: list[int] = []
    prior_shares: list[float] = []
    for chain in champs.values():
        for p, c in chain.items():
            games_per_patch[p] += c["games"]
            if c["games"] > 0:
                cell_games.append(c["games"])
                k = c["prior_a"] + c["prior_b"]
                prior_shares.append(k / (k + c["games"]))
    cell_games.sort()
    prior_shares.sort()

    def pct(sorted_vals, q):
        if not sorted_vals:
            return None
        i = min(len(sorted_vals) - 1, int(round(q / 100 * (len(sorted_vals) - 1))))
        return sorted_vals[i]

    return {
        "games_per_patch": games_per_patch,
        "n_champ_patch_cells_with_games": len(cell_games),
        "cell_games_pct": {q: pct(cell_games, q) for q in (10, 25, 50, 75, 90)},
        "prior_weight_share_pct": {q: round(pct(prior_shares, q), 3)
                                   for q in (10, 50, 90)},
    }


# --- offline self-test --------------------------------------------------------
def _selftest() -> bool:
    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            print(f"  FAIL: {msg}")
            ok = False

    # 40 games mostly inherits; 2000 games mostly stands alone
    agg = {"Inherit": {"1.1": [50, 100], "1.2": [24, 40]},
           "Standalone": {"1.1": [50, 100], "1.2": [1200, 2000]}}
    chains = fit_random_walk(agg, ["1.1", "1.2"], kappa0=50, kappa=200)
    m1 = chains["Inherit"]["1.1"]["mean"]
    check(chains["Inherit"]["1.2"]["prior_a"] == m1 * 200,
          "t>1 prior must carry the previous posterior mean")
    m_inh = chains["Inherit"]["1.2"]["mean"]        # 40 games at 60%
    m_std = chains["Standalone"]["1.2"]["mean"]     # 2000 games at 60%
    check(abs(m_inh - 0.5167) < 0.01, f"40-game champ should sit near the prior, got {m_inh:.4f}")
    check(abs(m_std - 0.5909) < 0.01, f"2000-game champ should sit near 0.59, got {m_std:.4f}")
    check(m_std > m_inh + 0.05, "more games must mean less shrinkage")

    # aggregation counts each participant-game exactly once across the row pair
    rows = [
        {"matchId": "M1", "patch": "1.1", "win": 1,
         "allies": {"TOP": "A", "MID": "B"}, "enemies": {"TOP": "C", "MID": "D"}},
        {"matchId": "M1", "patch": "1.1", "win": 0,
         "allies": {"TOP": "C", "MID": "D"}, "enemies": {"TOP": "A", "MID": "B"}},
    ]
    agg2 = aggregate_rows(rows)
    check(agg2["A"]["1.1"] == [1, 1] and agg2["C"]["1.1"] == [0, 1],
          f"aggregation wrong: {agg2}")

    # LOO subtracts exactly this match's contribution
    art = {"version": 1,
           "meta": {"patches": ["1.1"], "latest_patch": "1.1"},
           "champions": fit_random_walk({"A": {"1.1": [6, 10]}}, ["1.1"],
                                        kappa0=50, kappa=200)}
    s = ChampStrength(art)
    full = s.edge("A")
    loo_win = s.loo_view("1.1", {"A": 1}).edge("A")
    loo_loss = s.loo_view("1.1", {"A": 0}).edge("A")
    check(loo_win < full < loo_loss,
          f"LOO must remove the match's own outcome (win {loo_win}, full {full}, loss {loo_loss})")
    exp = (25 + 6 - 1) / (50 + 10 - 1) - 0.5
    check(abs(loo_win - exp) < 1e-12, f"LOO math wrong: {loo_win} != {exp}")
    check(s.loo_view("1.1", {}).edge("A") == full,
          "champ not in the match must get the plain posterior")
    check(s.edge("Unknown") is None, "unknown champ must be None (skipped)")

    # patch ordering: 16.9 before 16.10
    check(sorted(["16.10", "16.9", "16.2"], key=_patch_key)
          == ["16.2", "16.9", "16.10"], "numeric patch sort broken")

    print("  OK: champstats self-test passed" if ok else "  self-test FAILED")
    return ok


def main(argv=None):
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="lol-draft-champstats")
    ap.add_argument("cmd", nargs="?", choices=["build"], help="build the artifact")
    ap.add_argument("--selftest", action="store_true",
                    help="run the offline self-test and exit")
    ap.add_argument("--data", default=str(config.PROJECT_DIR / "data" / "games"
                                          / "labeled_games.jsonl"))
    ap.add_argument("--kappa0", type=float, default=KAPPA0)
    ap.add_argument("--kappa", type=float, default=KAPPA)
    ap.add_argument("--out", default=str(default_strength_path()))
    args = ap.parse_args(argv)

    if args.selftest:
        raise SystemExit(0 if _selftest() else 1)
    if args.cmd != "build":
        ap.print_help()
        return

    artifact = build_artifact(Path(args.data), kappa0=args.kappa0, kappa=args.kappa)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact), encoding="utf-8")
    meta, diag = artifact["meta"], diagnostics(artifact)
    print(f"=== CHAMP STRENGTH ARTIFACT (saved {out}) ===")
    print(f"  {meta['n_matches']} matches -> {meta['n_champions']} champions "
          f"x {len(meta['patches'])} patches "
          f"({meta['patches'][0]}..{meta['latest_patch']})")
    print(f"  kappa0={meta['kappa0']}  kappa={meta['kappa']}")
    print(f"  participant-games per patch: "
          f"{ {p: g for p, g in diag['games_per_patch'].items()} }")
    print(f"  per-champ-per-patch games (cells with games>0, "
          f"n={diag['n_champ_patch_cells_with_games']}): "
          f"p10/p25/p50/p75/p90 = "
          + "/".join(str(diag['cell_games_pct'][q]) for q in (10, 25, 50, 75, 90)))
    print(f"  prior weight share (shrinkage): p10/median/p90 = "
          + "/".join(str(diag['prior_weight_share_pct'][q]) for q in (10, 50, 90)))


if __name__ == "__main__":
    main()
