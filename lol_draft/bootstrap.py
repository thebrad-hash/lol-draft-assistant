"""Bootstrap the win-probability fit to quantify coefficient uncertainty.

The deployed model (`lol_draft.train`) gives a single point estimate of P(win)
per pick. Those coefficients were fit from finite, noisy games and carry real
error bars. This module resamples the TRAINING games WITH REPLACEMENT, refits
the same logistic model many times, and stores the whole coefficient ensemble
so the recommender can report a DISTRIBUTION of win probabilities instead of a
falsely precise number.

    python -m lol_draft.bootstrap [--data ...] [--patch all] [--rank diamond]
                                  [--iters 1000] [--C 1.0] [--seed 12345]
                                  [--out data/models/winprob_bootstrap.json]

Method (matches the constraints):
  - Resample whole GAMES, not feature rows. Each game is two anti-correlated
    perspective rows sharing a matchId; we resample matchIds with replacement to
    the same match count, then take BOTH rows of every drawn game. This reflects
    real sampling variance (which games we happened to observe), and—because each
    game contributes exactly one win-row and one loss-row—every resample stays
    perfectly class-balanced, so no refit degenerates.
  - Training data only. CIs come from resampling the training set; the held-out
    GroupKFold validation in `train.py` still checks calibration separately. We
    do NOT bootstrap the test set.
  - Fixed seed -> reproducible ensemble. --iters is a parameter.

Offline by design: this is the slow step (1000 sklearn refits). It runs once and
saves the ensemble; the live recommender just loads the coefficients (pure numpy
math, no sklearn) and evaluates them.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from . import config
from .model import WinProbEnsemble, WinProbModel, default_ensemble_path
from .store import Store
from .train import build_matrix, filter_patch, load_rows


def bootstrap_fit(X, y, groups, *, n_iter: int = 1000, C: float = 1.0,
                  seed: int = 12345, progress=None) -> tuple[list[WinProbModel], float]:
    """Refit the standardized logistic model on `n_iter` game-level bootstrap
    resamples. Returns (member models, wall_clock_seconds).

    `groups` labels each row with its matchId; resampling draws matchIds (whole
    games) with replacement to the original match count. `seed` makes the draw
    reproducible. `progress(i, n)` is an optional callback."""
    # matchId -> the row indices belonging to that game (both perspectives).
    match_to_rows: dict = defaultdict(list)
    for i, g in enumerate(groups):
        match_to_rows[g].append(i)
    matches = list(match_to_rows.keys())          # stable order
    row_lists = [np.asarray(match_to_rows[m]) for m in matches]
    n_matches = len(matches)

    rng = np.random.default_rng(seed)
    members: list[WinProbModel] = []
    t0 = time.perf_counter()
    for b in range(n_iter):
        drawn = rng.integers(0, n_matches, size=n_matches)   # games, with replacement
        idx = np.concatenate([row_lists[d] for d in drawn])
        Xb, yb = X[idx], y[idx]
        scaler = StandardScaler().fit(Xb)
        clf = LogisticRegression(C=C, max_iter=2000).fit(scaler.transform(Xb), yb)
        members.append(WinProbModel.from_sklearn(scaler, clf, meta={}))
        if progress:
            progress(b + 1, n_iter)
    elapsed = time.perf_counter() - t0
    return members, elapsed


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="lol-draft-bootstrap")
    ap.add_argument("--data", default=str(config.PROJECT_DIR / "data" / "games" / "labeled_games.jsonl"))
    ap.add_argument("--patch", default="all", help="auto (modal) | all | specific e.g. 16.8")
    ap.add_argument("--rank", default=config.DEFAULT_RANK)
    ap.add_argument("--iters", type=int, default=1000, help="bootstrap resamples (refits)")
    ap.add_argument("--C", type=float, default=1.0, help="L2 inverse-reg strength (match the deployed model)")
    ap.add_argument("--seed", type=int, default=12345, help="RNG seed -> reproducible ensemble")
    ap.add_argument("--champ-strength", choices=["machineloling", "own_data"],
                    default="machineloling", dest="champ_strength",
                    help="champ_strength feature source — MUST match the deployed "
                         "model's (see winprob.json meta champ_strength_source)")
    ap.add_argument("--out", default=str(default_ensemble_path()))
    args = ap.parse_args(argv)

    rows, patch_label = filter_patch(load_rows(Path(args.data)), args.patch)
    n_matches = len({r["matchId"] for r in rows})
    print(f"=== BOOTSTRAP FIT ===")
    print(f"  data: {args.data}")
    print(f"  patch: {patch_label}   rows: {len(rows)}   matches (resample unit): {n_matches}")
    print(f"  iters: {args.iters}   C: {args.C}   seed: {args.seed}")

    strength = None
    if args.champ_strength == "own_data":
        from .champstats import ChampStrength
        strength = ChampStrength.load()
        print(f"  champ_strength: own-data EB posterior (LOO per row)")

    with Store() as store:
        X, y, groups, _cov = build_matrix(store, rows, args.rank, strength=strength)

    def progress(i, n):
        if i == 1 or i % 100 == 0 or i == n:
            print(f"    refit {i}/{n}", flush=True)

    members, elapsed = bootstrap_fit(
        X, y, groups, n_iter=args.iters, C=args.C, seed=args.seed, progress=progress)

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "n_iter": args.iters, "seed": args.seed, "C": args.C,
        "patch": patch_label, "rank": args.rank,
        "n_rows": len(rows), "n_matches": n_matches,
        "champ_strength_source": ("own_data_eb" if strength is not None
                                  else "machineloling"),
        "resample_unit": "game (matchId)",
        "wall_clock_s": elapsed,
        "per_refit_ms": 1000.0 * elapsed / max(args.iters, 1),
    }
    ensemble = WinProbEnsemble(members=members, meta=meta)
    out = ensemble.save(args.out)

    # --- coefficient spread (sanity + the headline error bars) ---
    names = members[0].feature_names
    raw = np.array([[m.raw_coefficients()[1][k] for k in names] for m in members])
    b0 = np.array([m.raw_coefficients()[0] for m in members])
    print(f"\n=== ENSEMBLE  (saved {out}) ===")
    print(f"  members: {len(members)}")
    print(f"  WALL-CLOCK for {args.iters} refits: {elapsed:.1f}s "
          f"({meta['per_refit_ms']:.1f} ms/refit)")
    print(f"\n  raw-feature coefficient spread (5th / 50th / 95th pct across resamples):")
    print(f"  {'feature':<15}{'p5':>10}{'median':>10}{'p95':>10}{'sd':>10}")
    print(f"  {'intercept':<15}{np.percentile(b0,5):>10.3f}{np.percentile(b0,50):>10.3f}"
          f"{np.percentile(b0,95):>10.3f}{b0.std():>10.3f}")
    for j, k in enumerate(names):
        col = raw[:, j]
        print(f"  {k:<15}{np.percentile(col,5):>10.3f}{np.percentile(col,50):>10.3f}"
              f"{np.percentile(col,95):>10.3f}{col.std():>10.3f}")
    # Does any coefficient's 90% interval cross zero? (a feature we can't sign)
    print("\n  90% interval crosses zero?")
    for j, k in enumerate(names):
        col = raw[:, j]
        crosses = np.percentile(col, 5) < 0 < np.percentile(col, 95)
        print(f"    {k:<15}{'YES — sign uncertain' if crosses else 'no — sign stable'}")


if __name__ == "__main__":
    main()
