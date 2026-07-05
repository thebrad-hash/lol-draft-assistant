"""Fixed pick-interval benchmark (WS3 deliverable).

A committed file of ~50 representative mid-draft states
(data/benchmarks/pick_states.json: varied phases — early picks, mid-draft,
last pick — plus lobby-pool scenarios, generated deterministically from real
collected games) and a runner that reports, with z-cell sampling OFF vs ON:

  - median and p90 win-prob interval width across the top-5 candidates/state;
  - the fraction of ADJACENT top-5 candidate pairs "too close to call" at the
    current threshold (neither side wins >= WINPROB_TIE_THRESHOLD of the
    paired resamples).

Wider intervals and more ties are the EXPECTED effect of WS3 — the report
exists so the tie threshold can be retuned deliberately (an owner decision;
this module never touches the threshold default).

    python -m lol_draft.benchmark                 # run OFF vs ON (artifact c)
    python -m lol_draft.benchmark --c 15.0        # explicit ON constant
    python -m lol_draft.benchmark regen           # regenerate the states file
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .model import WinProbEnsemble, WinProbModel, default_ensemble_path, default_model_path
from .scoring import DraftState
from .store import Store
from .winprob import TIE_THRESHOLD_DEFAULT, rank_candidates_uncertain

STATES_PATH = config.PROJECT_DIR / "data" / "benchmarks" / "pick_states.json"
GEN_SEED = 20260705
N_STATES = 50
TOP_K = 5

# phase -> (n allies besides me, n enemies); "last" = everyone else locked
PHASES = {"early": (1, 2), "mid": (3, 3), "last": (4, 5)}


def gen_states(data_path: Path, *, n: int = N_STATES, seed: int = GEN_SEED) -> list[dict]:
    """Deterministically derive draft states from real collected matches: take a
    match's actual 5v5, reveal only a phase-sized subset, pick an open role.
    Bans come from the NEXT sampled match's champions (real-ish, never collides
    with this state's picks). Every 5th state is a lobby-pool scenario: the
    actual pick plus 7 other role-playable champions."""
    from .champstats import load_rows
    rng = random.Random(seed)
    rows = [r for r in load_rows(data_path) if r["side"] == 100
            and len(r["allies"]) == 5 and len(r["enemies"]) == 5]
    rng.shuffle(rows)
    picked = rows[: n * 2]  # extra matches: bans donor pool

    with Store() as store:
        states = []
        phase_cycle = ["early", "mid", "mid", "last"]  # 25% early, 50% mid, 25% last
        for i in range(n):
            r = picked[i]
            donor = picked[(i + n) % len(picked)]
            phase = phase_cycle[i % len(phase_cycle)]
            n_allies, n_enemies = PHASES[phase]

            roles = list(config.ROLES)
            my_role = rng.choice(roles)
            other_roles = [x for x in roles if x != my_role]
            rng.shuffle(other_roles)
            ally_roles = sorted(other_roles[:n_allies])
            enemy_roles = (sorted(rng.sample(roles, n_enemies))
                           if n_enemies < 5 else roles)

            used = set(r["allies"].values()) | set(r["enemies"].values())
            bans = [c for c in list(donor["allies"].values())
                    + list(donor["enemies"].values()) if c not in used][:6]

            pool = None
            if i % 5 == 4:  # lobby-pool scenario
                role_champs = [c for c in store.role_champions(my_role)
                               if c not in used and c not in bans]
                rng.shuffle(role_champs)
                pool = sorted(set([r["allies"][my_role]] + role_champs[:7]))

            states.append({
                "id": f"state_{i:02d}",
                "phase": phase,
                "source_match": r["matchId"],
                "patch": r["patch"],
                "my_role": my_role,
                "allies": {ro: r["allies"][ro] for ro in ally_roles},
                "enemies": {ro: r["enemies"][ro] for ro in enemy_roles},
                "bans": bans,
                "pool": pool,
            })
    return states


def _pairwise_p(a: list[float], b: list[float]) -> float:
    wins = 0.0
    for x, y in zip(a, b):
        if x > y:
            wins += 1.0
        elif x == y:
            wins += 0.5
    return wins / len(a)


def run_benchmark(states: list[dict], configs: dict[str, float | None],
                  *, tie_threshold: float = TIE_THRESHOLD_DEFAULT) -> dict:
    """{config label: metrics} over the fixed states. `configs` maps a label to
    the z_cell_c passed through (None = WS3 sampling off)."""
    model = WinProbModel.load(default_model_path())
    ensemble = WinProbEnsemble.load(default_ensemble_path())
    from .champstats import strength_for_meta
    strength = strength_for_meta(model.meta)

    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(),
                 "n_states": len(states), "top_k": TOP_K,
                 "tie_threshold": tie_threshold, "configs": {}}
    with Store() as store:
        for label, c in configs.items():
            widths: list[float] = []
            ties = 0
            pairs = 0
            top_ties = 0        # tied_with_top among ranks 2..TOP_K — the UI badge
            top_pairs = 0
            per_state = []
            for st in states:
                ds = DraftState(my_role=st["my_role"], enemies=st["enemies"],
                                allies=st["allies"], bans=list(st["bans"]),
                                pool=st["pool"])
                vecs: list = []
                results, _ = rank_candidates_uncertain(
                    store, ds, ensemble, rank=config.DEFAULT_RANK,
                    point_model=model, tie_threshold=tie_threshold,
                    strength=strength, z_cell_c=c, _vectors_out=vecs)
                top = results[:TOP_K]
                st_widths = [r.p_hi - r.p_lo for r in top]
                widths.extend(st_widths)
                top_ties += sum(1 for r in top[1:] if r.tied_with_top)
                top_pairs += len(top) - 1
                st_ties = 0
                for i in range(len(top) - 1):
                    p = _pairwise_p(vecs[i][1], vecs[i + 1][1])
                    pairs += 1
                    if max(p, 1.0 - p) < tie_threshold:
                        ties += 1
                        st_ties += 1
                per_state.append({"id": st["id"], "phase": st["phase"],
                                  "n_candidates": len(results),
                                  "median_width": round(_median(st_widths), 4),
                                  "adjacent_ties": st_ties})
            widths.sort()
            out["configs"][label] = {
                "z_cell_c": c,
                "median_width": round(_median(widths), 4),
                "p90_width": round(_pct(widths, 90), 4),
                "adjacent_pairs": pairs,
                "adjacent_ties": ties,
                "tie_fraction": round(ties / pairs, 4) if pairs else None,
                "tied_with_top_fraction": (round(top_ties / top_pairs, 4)
                                           if top_pairs else None),
                "per_state": per_state,
            }
    return out


def _median(vals: list[float]) -> float:
    return _pct(sorted(vals), 50)


def _pct(sorted_vals: list[float], q: float) -> float:
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    if n == 1:
        return sorted_vals[0]
    rank = (q / 100.0) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (rank - lo) * (sorted_vals[hi] - sorted_vals[lo])


def print_report(report: dict) -> None:
    print(f"=== PICK-INTERVAL BENCHMARK ({report['n_states']} states, "
          f"top-{report['top_k']}, tie threshold {report['tie_threshold']}) ===")
    print(f"  {'config':<14}{'median width':>14}{'p90 width':>12}"
          f"{'adjacent ties':>16}{'tied w/ top':>13}")
    for label, m in report["configs"].items():
        frac = f"{m['adjacent_ties']}/{m['adjacent_pairs']} ({m['tie_fraction']*100:.0f}%)"
        tt = (f"{m['tied_with_top_fraction']*100:.0f}%"
              if m.get("tied_with_top_fraction") is not None else "n/a")
        print(f"  {label:<14}{m['median_width']:>14.4f}{m['p90_width']:>12.4f}"
              f"{frac:>16}{tt:>13}")


def main(argv=None):
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="lol-draft-benchmark")
    ap.add_argument("cmd", nargs="?", choices=["regen"],
                    help="regenerate the committed states file (fixed seed)")
    ap.add_argument("--data", default=str(config.PROJECT_DIR / "data" / "games"
                                          / "labeled_games.jsonl"))
    ap.add_argument("--c", type=float, default=None,
                    help="z-cell noise constant for the ON config "
                         "(default: data/models/zcell_noise.json)")
    ap.add_argument("--out", default=str(config.PROJECT_DIR / "data" / "reports"
                                         / "zcell_benchmark.json"))
    args = ap.parse_args(argv)

    if args.cmd == "regen":
        states = gen_states(Path(args.data))
        STATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATES_PATH.write_text(json.dumps(states, indent=1), encoding="utf-8")
        phases = {}
        for s in states:
            phases[s["phase"]] = phases.get(s["phase"], 0) + 1
        print(f"wrote {len(states)} states -> {STATES_PATH}")
        print(f"  phases: {phases}, pool scenarios: "
              f"{sum(1 for s in states if s['pool'])}")
        return

    states = json.loads(STATES_PATH.read_text(encoding="utf-8"))
    c = args.c
    if c is None:
        from .cellnoise import load_c
        c = load_c()
    if c is None:
        print("No z-cell constant available (no artifact / env); "
              "run `python -m lol_draft.cellnoise fit` first.")
        raise SystemExit(2)
    report = run_benchmark(states, {"zcell OFF": None, "zcell ON": c})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print_report(report)
    print(f"\n  report: {out}")


if __name__ == "__main__":
    main()
