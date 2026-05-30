"""Fit + validate the calibrated win-probability model (Steps 3-4).

    python -m lol_draft.train [--data data/games/labeled_games.jsonl]
                              [--patch auto|all|16.8] [--rank diamond]
                              [--folds 5] [--C 1.0] [--out data/models/winprob.json]

Every performance number printed is OUT-OF-SAMPLE: we use GroupKFold on
`matchId` so a game's two anti-correlated rows never split across train/test,
and report metrics on held-out folds only. We compare against the old additive-z
scoring (calibrated) and an intercept-only null, then fit the final model on all
data and save it (with its CV provenance) for the live recommender.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

from . import config
from .features import FEATURE_NAMES, draft_features
from .model import WinProbModel, default_model_path
from .store import Store

# Old additive-z weights (config.DEFAULT_WEIGHTS) mapped onto the team features.
# blindability has no team-level analogue, so the baseline is the 3 z-terms the
# old engine actually summed across the board.
ADDITIVE_Z_WEIGHTS = {"lane_z": config.DEFAULT_WEIGHTS["in_lane"],
                      "counter_z": config.DEFAULT_WEIGHTS["out_of_lane"],
                      "synergy_z": config.DEFAULT_WEIGHTS["synergy"]}

PLAIN = {
    "lane_z": "your team's head-to-head lane matchups",
    "counter_z": "your champs' cross-lane (off-role) matchups vs their comp",
    "synergy_z": "your team's synergy edge over theirs",
    "champ_strength": "your champions' raw win-rate edge over theirs",
}


def load_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_matrix(store: Store, rows: list[dict], rank: str):
    """rows -> (X, y, groups, coverage). One feature vector per row, via the
    SAME draft_features the live tool uses (train/inference parity)."""
    X, y, groups, cov = [], [], [], []
    for r in rows:
        f = draft_features(store, r["allies"], r["enemies"], rank)
        X.append(f.vector())
        y.append(int(r["win"]))
        groups.append(r["matchId"])
        cov.append(f.coverage)
    return np.array(X, float), np.array(y, int), np.array(groups), cov


def oof_predictions(X, y, groups, n_splits, C):
    """Out-of-fold P(win) for the standardized logistic model."""
    gkf = GroupKFold(n_splits=n_splits)
    oof = np.zeros(len(y))
    for tr, te in gkf.split(X, y, groups):
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(C=C, max_iter=2000).fit(sc.transform(X[tr]), y[tr])
        oof[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
    return oof


def oof_additive_z(X, y, groups, n_splits):
    """Baseline: old additive-z total, calibrated to a probability via a
    1-feature logistic per fold (best-case for the current scoring)."""
    w = np.array([ADDITIVE_Z_WEIGHTS.get(k, 0.0) for k in FEATURE_NAMES])
    score = (X * w).sum(axis=1).reshape(-1, 1)
    gkf = GroupKFold(n_splits=n_splits)
    oof = np.zeros(len(y))
    for tr, te in gkf.split(score, y, groups):
        clf = LogisticRegression(max_iter=2000).fit(score[tr], y[tr])
        oof[te] = clf.predict_proba(score[te])[:, 1]
    return oof


def oof_null(y, groups, n_splits):
    """Floor: predict the train-fold base rate for everyone."""
    gkf = GroupKFold(n_splits=n_splits)
    oof = np.zeros(len(y))
    for tr, te in gkf.split(y.reshape(-1, 1), y, groups):
        oof[te] = y[tr].mean()
    return oof


def metrics(y, p) -> dict:
    return {
        "log_loss": log_loss(y, p),
        "accuracy": accuracy_score(y, (p >= 0.5).astype(int)),
        "auc": roc_auc_score(y, p),
        "brier": brier_score_loss(y, p),
    }


def calibration_table(y, p, lo=0.30, hi=0.70, width=0.05):
    """Reliability bins: predicted vs actual win rate. Returns (rows, ece)."""
    edges = list(np.arange(lo, hi + 1e-9, width))
    bins = [(-np.inf, edges[0])] + list(zip(edges[:-1], edges[1:])) + [(edges[-1], np.inf)]
    table, ece, n = [], 0.0, len(y)
    for a, b in bins:
        m = (p >= a) & (p < b)
        c = int(m.sum())
        if c == 0:
            continue
        pred, act = float(p[m].mean()), float(y[m].mean())
        table.append({"lo": a, "hi": b, "n": c, "pred": pred, "actual": act})
        ece += (c / n) * abs(pred - act)
    return table, ece


def main(argv=None):
    # Windows consoles default to cp1252; ensure non-ASCII output never crashes.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="lol-draft-train")
    ap.add_argument("--data", default=str(config.PROJECT_DIR / "data" / "games" / "labeled_games.jsonl"))
    ap.add_argument("--patch", default="auto", help="auto (modal) | all | specific e.g. 16.8")
    ap.add_argument("--rank", default=config.DEFAULT_RANK)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--C", type=float, default=1.0, help="L2 inverse-reg strength (large ~ unregularized)")
    ap.add_argument("--out", default=str(default_model_path()))
    args = ap.parse_args(argv)

    rows = load_rows(Path(args.data))
    patch_dist = Counter(r["patch"] for r in rows)
    if args.patch == "auto":
        patch = patch_dist.most_common(1)[0][0]
        rows = [r for r in rows if r["patch"] == patch]
        patch_label = patch
    elif args.patch == "all":
        patch_label = "all"
    else:
        rows = [r for r in rows if r["patch"] == args.patch]
        patch_label = args.patch

    print(f"=== DATA ===")
    print(f"  file: {args.data}")
    print(f"  patch filter: {patch_label}  (full distribution: "
          f"{dict(patch_dist.most_common())})")
    print(f"  rows: {len(rows)}  matches: {len(set(r['matchId'] for r in rows))}")
    if len(rows) < 200:
        print("  ! WARNING: very few rows — metrics below are NOT statistically meaningful.")

    with Store() as store:
        X, y, groups, cov = build_matrix(store, rows, args.rank)

    print(f"  win rate: {y.mean():.3f}  (should be ~0.5 — both perspectives present)")
    full = sum(1 for c in cov if c["lane_z"] == 5 and c["counter_z"] == 20
               and c["synergy_z"] == 20 and c["champ_strength"] == 10)
    print(f"  fully-covered drafts: {full}/{len(rows)} ({100*full/len(rows):.0f}%) — "
          f"rest shrink toward neutral over full 5v5 denominators (kept, not dropped)")
    print("  feature means / sd:")
    for i, k in enumerate(FEATURE_NAMES):
        print(f"    {k:14} mean={X[:,i].mean():+.4f}  sd={X[:,i].std():.4f}")
    corr = np.corrcoef(X.T)
    print("  feature correlations (collinearity check):")
    print("           " + " ".join(f"{k[:8]:>8}" for k in FEATURE_NAMES))
    for i, k in enumerate(FEATURE_NAMES):
        print(f"    {k[:8]:8} " + " ".join(f"{corr[i,j]:+8.2f}" for j in range(len(FEATURE_NAMES))))

    # --- out-of-sample evaluation (GroupKFold on matchId) ---
    print(f"\n=== OUT-OF-SAMPLE ({args.folds}-fold GroupKFold on matchId) ===")
    oof_model = oof_predictions(X, y, groups, args.folds, args.C)
    oof_base = oof_additive_z(X, y, groups, args.folds)
    oof_nul = oof_null(y, groups, args.folds)
    mm, mb, mn = metrics(y, oof_model), metrics(y, oof_base), metrics(y, oof_nul)
    print(f"  {'model':<22}{'log_loss':>10}{'accuracy':>10}{'AUC':>8}{'Brier':>9}")
    for name, m in (("logistic (4 features)", mm),
                    ("additive-z (baseline)", mb),
                    ("null (base rate)", mn)):
        print(f"  {name:<22}{m['log_loss']:>10.4f}{m['accuracy']:>10.4f}"
              f"{m['auc']:>8.4f}{m['brier']:>9.4f}")

    # --- calibration (held-out) ---
    table, ece = calibration_table(y, oof_model)
    print(f"\n=== CALIBRATION (held-out predictions; ECE={ece:.4f}) ===")
    print(f"  {'bucket':<14}{'n':>6}{'predicted':>11}{'actual':>9}{'gap':>8}")
    for t in table:
        lab = f"{t['lo']*100:.0f}-{t['hi']*100:.0f}%" if np.isfinite(t['lo']) and np.isfinite(t['hi']) \
            else ("<30%" if not np.isfinite(t['lo']) else ">=70%")
        print(f"  {lab:<14}{t['n']:>6}{t['pred']*100:>10.1f}%{t['actual']*100:>8.1f}%"
              f"{(t['pred']-t['actual'])*100:>+7.1f}%")

    # --- final fit on ALL data, save, interpret ---
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(C=args.C, max_iter=2000).fit(scaler.transform(X), y)
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "n_rows": len(rows), "n_matches": len(set(groups.tolist())),
        "patch": patch_label, "rank": args.rank, "C": args.C, "folds": args.folds,
        "cv_logloss": mm["log_loss"], "cv_accuracy": mm["accuracy"],
        "cv_auc": mm["auc"], "cv_brier": mm["brier"], "cv_ece": ece,
        "baseline_logloss": mb["log_loss"], "null_logloss": mn["log_loss"],
    }
    model = WinProbModel.from_sklearn(scaler, clf, meta)
    out = model.save(args.out)

    print(f"\n=== FITTED MODEL  (saved {out}) ===")
    print(f"  intercept (standardized): {model.intercept:+.4f}  "
          f"(≈0 expected by symmetry; |.|>~0.1 flags a side/data bias)")
    sd = scaler.scale_
    print(f"  {'feature':<15}{'coef(std)':>11}{'odds/SD':>10}   interpretation")
    order = sorted(range(len(FEATURE_NAMES)), key=lambda i: -abs(model.coef[i]))
    for i in order:
        k = FEATURE_NAMES[i]
        c = model.coef[i]
        print(f"  {k:<15}{c:>+11.4f}{np.exp(c):>10.3f}   +1 SD ({sd[i]:.3f}) of {PLAIN[k]}")
    b0, raw = model.raw_coefficients()
    print(f"\n  raw-feature equation (deployable):")
    terms = "  ".join(f"{raw[k]:+.3f}*{k}" for k in FEATURE_NAMES)
    print(f"    logit(win) = {b0:+.3f}  {terms}")
    print(f"    P(win) = 1 / (1 + e^-logit)")


if __name__ == "__main__":
    main()
