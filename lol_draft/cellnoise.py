"""Calibrate the z-cell noise constant `c` (WS3, offline).

The serve-time noise model (`winprob.ZCellNoise`) needs one global constant:
s²_cell = c / N̂_cell with N̂ = G_rolepair · PR_A · PR_B. This module fits and
persists `c` to data/models/zcell_noise.json (committed — the serve path reads
it; sampling stays OFF while the artifact is absent).

Two ways to get `c`, in preference order:

1. SNAPSHOT-CALIBRATED (needs >= 2 archived snapshots, WS1): under
   stationarity, E[(z1 - z2)²] = s²_1 + s²_2 = c · (1/N̂_1 + 1/N̂_2), so `c` is
   a through-the-origin regression of squared cross-snapshot differences on
   (1/N̂_1 + 1/N̂_2). The fit is the MEDIAN of per-cell ratios d²/x divided by
   0.4549 (the median of chi²_1 — each d² is c·x·chi²_1 under the model), so
   genuine drift outliers (reworks) can't inflate it the way a mean would.

2. FALLBACK DEFAULT (single snapshot): the single `c` that best satisfies
   "median-N̂ cell gets s ≈ 0.5 AND 10th-percentile cell gets s ≈ 1.0" in
   log-space — the geometric mean of the two implied constants (0.25·N̂_med and
   1.0·N̂_p10). The implied s at both anchors is reported so the compromise is
   visible. THIS IS A HEURISTIC, as is the N̂ proxy itself — both are
   documented as such in the artifact.

Env overrides (read by `load_c`): WINPROB_ZCELL=off disables sampling entirely;
WINPROB_ZCELL_C=<float> overrides the artifact's constant.

CLI:  python -m lol_draft.cellnoise fit [--a SNAP --b SNAP] [--rank diamond]
      python -m lol_draft.cellnoise --selftest
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .model import models_dir

CHI2_1_MEDIAN = 0.4549364231195728   # median of a chi-square with 1 dof

# Fallback anchors (spec): s at the median-N̂ cell and at the p10-N̂ cell.
FALLBACK_S_MEDIAN = 0.5
FALLBACK_S_P10 = 1.0


def default_noise_path() -> Path:
    return models_dir() / "zcell_noise.json"


# --- N̂ proxy over a playrate table (store-independent so it can run on an
# archived snapshot's champions.json as easily as on the live draft.db) -------
def _n_hat(g_a: int, g_b: int, pr_a: float, pr_b: float) -> float:
    return 0.5 * (g_a + g_b) * pr_a * pr_b


def n_hat_distribution(store, rank: str) -> list[float]:
    """N̂ for every distinct (role_a, role_b, champ_a, champ_b) pairing in the
    cells table (mode-independent — the proxy doesn't distinguish matchup from
    synergy). Offline use: own connection or under the caller's lock."""
    pr = {r: store.pick_rates(rank, r) for r in config.ROLES}
    g = {r: store.role_games(rank, r) for r in config.ROLES}
    cur = store.con.execute(
        "SELECT DISTINCT role_a, role_b, champ_a, champ_b FROM cells")
    out = []
    for row in cur:
        out.append(_n_hat(g[row["role_a"]], g[row["role_b"]],
                          pr[row["role_a"]].get(row["champ_a"], 0.0),
                          pr[row["role_b"]].get(row["champ_b"], 0.0)))
    return out


def _percentile(sorted_vals: list[float], q: float) -> float:
    n = len(sorted_vals)
    rank = (q / 100.0) * (n - 1)
    lo = int(math.floor(rank))
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (rank - lo) * (sorted_vals[hi] - sorted_vals[lo])


def fallback_c(n_hats: list[float]) -> dict:
    """The heuristic single-snapshot default (see module docstring)."""
    pos = sorted(x for x in n_hats if x > 0)
    n_med, n_p10 = _percentile(pos, 50), _percentile(pos, 10)
    c_med = FALLBACK_S_MEDIAN ** 2 * n_med    # s(median) = 0.5 exactly
    c_p10 = FALLBACK_S_P10 ** 2 * n_p10       # s(p10)    = 1.0 exactly
    c = math.sqrt(c_med * c_p10)              # log-space compromise
    return {
        "c": c,
        "method": "fallback_default",
        "n_hat_median": n_med,
        "n_hat_p10": n_p10,
        "n_cells": len(n_hats),
        "n_cells_zero_n_hat": sum(1 for x in n_hats if x <= 0),
        "implied_s": {"median_n_cell": math.sqrt(c / n_med),
                      "p10_n_cell": math.sqrt(c / n_p10)},
        "anchors": {"s_median_target": FALLBACK_S_MEDIAN,
                    "s_p10_target": FALLBACK_S_P10},
    }


def fit_c_from_snapshots(dir_a: Path, dir_b: Path, rank: str) -> dict:
    """Snapshot-calibrated `c` (see module docstring). Decodes both snapshots'
    matrices AND playrates so each side gets its own N̂."""
    from . import decode
    from .snapshot import audit_snapshots  # cells come from the audit pass

    pr_g = []
    for d in (dir_a, dir_b):
        playrates = decode.load_playrates(d / "champions.json")
        by_role = playrates.get(rank, {})
        if not by_role:
            raise ValueError(f"snapshot {d.name} has no '{rank}' playrates")
        pr = {role: {c_: v["pick_rate"] for c_, v in champs.items()}
              for role, champs in by_role.items()}
        g = {role: sum(v["games"] for v in champs.values())
             for role, champs in by_role.items()}
        pr_g.append((pr, g))

    report, _, cells_path = audit_snapshots(dir_a, dir_b)
    import gzip
    with gzip.open(cells_path, "rt", encoding="utf-8") as f:
        cells = json.load(f)["cells"]

    ratios = []
    skipped = 0
    for i in range(len(cells["z_a"])):
        ra, rb = cells["role_a"][i], cells["role_b"][i]
        a, b = cells["champ_a"][i], cells["champ_b"][i]
        x = 0.0
        for pr, g in pr_g:
            nh = _n_hat(g.get(ra, 0), g.get(rb, 0),
                        pr.get(ra, {}).get(a, 0.0), pr.get(rb, {}).get(b, 0.0))
            if nh <= 0:
                x = 0.0
                break
            x += 1.0 / nh
        if x <= 0:
            skipped += 1
            continue
        d2 = (cells["z_a"][i] - cells["z_b"][i]) ** 2
        ratios.append(d2 / x)
    if len(ratios) < 100:
        raise ValueError(f"only {len(ratios)} usable cells — refusing to fit")
    ratios.sort()
    med = _percentile(ratios, 50)
    if med < 1e-9:
        # machineloling republishes playrates often but the interaction
        # matrices rarely — two snapshots straddling no matrix regeneration are
        # byte-identical and carry zero calibration signal. (Observed 2026-07:
        # May and July matrices.bin had the same SHA-256.)
        raise ValueError(
            "snapshots' z matrices are identical/near-identical — no "
            "cross-snapshot variation to calibrate from; keep the fallback c "
            "and re-fit after machineloling actually regenerates matrices.bin")
    c = med / CHI2_1_MEDIAN
    return {
        "c": c,
        "method": "snapshot_calibrated",
        "snapshots": [dir_a.name, dir_b.name],
        "n_cells_used": len(ratios),
        "n_cells_skipped_no_n_hat": skipped,
        "estimator": "median(d^2 / (1/N1+1/N2)) / chi2_1_median",
        "audit_min_pearson": report.get("min_pearson"),
    }


def load_c(path: Path | None = None) -> float | None:
    """The serve path's `c`, or None when z-cell sampling should be off.

    Off when: WINPROB_ZCELL=off, or no artifact and no WINPROB_ZCELL_C. The
    env var overrides the artifact so the constant is tunable on Vercel
    without a redeploy."""
    if os.environ.get("WINPROB_ZCELL", "").strip().lower() == "off":
        return None
    env_c = os.environ.get("WINPROB_ZCELL_C")
    if env_c:
        try:
            v = float(env_c)
            return v if v > 0 else None
        except ValueError:
            pass
    p = path or default_noise_path()
    try:
        art = json.loads(p.read_text(encoding="utf-8"))
        v = float(art["c"])
        return v if v > 0 else None
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError):
        return None


# --- offline self-test --------------------------------------------------------
def _selftest() -> bool:
    import random

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            print(f"  FAIL: {msg}")
            ok = False

    # fallback: exact anchor recovery + geometric-mean compromise
    n_hats = [100.0] * 50 + [400.0] * 50   # p10=100, median~400 (roughly)
    fb = fallback_c(sorted(n_hats))
    c_expected = math.sqrt((0.25 * fb["n_hat_median"]) * (1.0 * fb["n_hat_p10"]))
    check(abs(fb["c"] - c_expected) < 1e-9, "fallback c must be the geometric mean")
    s_med, s_p10 = fb["implied_s"]["median_n_cell"], fb["implied_s"]["p10_n_cell"]
    check(s_p10 > s_med, "smaller N̂ must imply larger s")

    # v_cell bound + N̂->0 limit (via the serve-side class, against a stub store)
    class StubStore:
        def pick_rates(self, rank, role):
            return {"A": 0.05, "B": 0.02}   # C absent -> N̂ = 0

        def role_games(self, rank, role):
            return 100_000

    from .winprob import ZCellNoise
    zn = ZCellNoise(StubStore(), "diamond", n=200, c=50.0, seed=1)
    v_ab = zn.v_cell("TOP", "MID", "A", "B")
    check(0.0 < v_ab < 1.0, f"v_cell must be in (0,1), got {v_ab}")
    # N̂ = 100k*0.05*0.02 = 100 -> s2=0.5 -> v = 1/3
    check(abs(v_ab - (0.5 / 1.5)) < 1e-9, f"v_cell math wrong: {v_ab}")
    check(zn.v_cell("TOP", "MID", "A", "C") == 1.0, "N̂=0 must give v=1 (prior sd)")
    d1 = zn.draws("matchup", "TOP", "MID", "A", "B")
    d2 = zn.draws("matchup", "TOP", "MID", "A", "B")
    check(d1 is d2, "same cell must return the cached (identical) draws")
    d3 = zn.draws("synergy", "TOP", "MID", "A", "B")
    check(d1 != d3, "different mode = different cell = different draws")
    var = sum(x * x for x in d1) / len(d1)
    check(abs(var - v_ab) < 0.15 * v_ab + 0.05, f"draw variance {var} != v_cell {v_ab}")
    s = zn.sum_series([("matchup", "TOP", "MID", "A", "B", 1.0),
                       ("matchup", "TOP", "MID", "A", "B", -1.0)])
    check(all(x == 0.0 for x in s), "sign +1/-1 of the same cell must cancel")

    # median-ratio estimator recovers a known c (with rework outliers thrown in)
    rng = random.Random(99)
    c_true = 40.0
    ratios = []
    for _ in range(4000):
        nh1, nh2 = rng.uniform(20, 2000), rng.uniform(20, 2000)
        x = 1 / nh1 + 1 / nh2
        d = rng.gauss(0, math.sqrt(c_true * x))
        ratios.append(d * d / x)
    for _ in range(200):   # 5% rework-sized outliers
        ratios.append(rng.uniform(50, 400) * c_true)
    ratios.sort()
    c_hat = _percentile(ratios, 50) / CHI2_1_MEDIAN
    check(abs(c_hat - c_true) / c_true < 0.15,
          f"median estimator off: {c_hat:.1f} vs true {c_true}")

    print("  OK: cellnoise self-test passed" if ok else "  self-test FAILED")
    return ok


def main(argv=None):
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="lol-draft-cellnoise")
    ap.add_argument("cmd", nargs="?", choices=["fit"],
                    help="fit c and write data/models/zcell_noise.json")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--a", help="older snapshot dir (with --b: snapshot-calibrated fit)")
    ap.add_argument("--b", help="newer snapshot dir")
    ap.add_argument("--rank", default=config.DEFAULT_RANK)
    ap.add_argument("--out", default=str(default_noise_path()))
    args = ap.parse_args(argv)

    if args.selftest:
        raise SystemExit(0 if _selftest() else 1)
    if args.cmd != "fit":
        ap.print_help()
        return

    from .snapshot import list_snapshots
    result = None
    if args.a and args.b:
        result = fit_c_from_snapshots(Path(args.a), Path(args.b), args.rank)
    else:
        snaps = list_snapshots()
        if len(snaps) >= 2:
            try:
                result = fit_c_from_snapshots(snaps[-2][0], snaps[-1][0], args.rank)
            except ValueError as e:
                print(f"  ! snapshot calibration unavailable: {e}")
        else:
            print(f"  {len(snaps)} snapshot(s) archived — need 2 for calibration.")
        if result is None:
            print("  using the heuristic fallback default.")
            from .store import Store
            with Store() as store:
                result = fallback_c(n_hat_distribution(store, args.rank))

    result["rank"] = args.rank
    result["built_at"] = datetime.now(timezone.utc).isoformat()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"=== Z-CELL NOISE CONSTANT (saved {out}) ===")
    for k, v in result.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
