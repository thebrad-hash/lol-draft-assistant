"""Command-line interface for the LoL Draft Assistant.

Examples
--------
  python -m lol_draft.cli build                 # download + build the store
  python -m lol_draft.cli recommend -r TOP \\
        -e MID=Ahri -e JUNGLE=LeeSin \\
        -a ADC=Jinx -a SUP=Thresh -b Darius --explain
  python -m lol_draft.cli weights --set in_lane=0.8
  python -m lol_draft.cli info
"""
from __future__ import annotations

import argparse
import sys

from . import config
from .scoring import DraftState, score_draft
from .store import Store, build_db

COMP_ORDER = ["in_lane", "out_of_lane", "synergy", "blindability"]
COMP_LABEL = {"in_lane": "In-lane", "out_of_lane": "Out-lane",
              "synergy": "Synergy", "blindability": "Blind"}
WEIGHT_ALIASES = {
    "in": "in_lane", "in_lane": "in_lane", "inlane": "in_lane",
    "out": "out_of_lane", "out_of_lane": "out_of_lane", "outlane": "out_of_lane",
    "syn": "synergy", "synergy": "synergy",
    "blind": "blindability", "blindability": "blindability",
}


# --- helpers ---------------------------------------------------------------
def _build_resolver(store: Store):
    canon: dict[str, str] = {}
    for name in store.all_champions():
        canon["".join(ch for ch in name.lower() if ch.isalnum())] = name

    def resolve(s: str) -> str:
        key = "".join(ch for ch in s.lower() if ch.isalnum())
        return canon.get(key, s)

    return resolve


def _parse_assignments(items, resolve) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"Expected ROLE=CHAMPION, got {it!r}")
        role, champ = it.split("=", 1)
        out[config.normalize_role(role)] = resolve(champ.strip())
    return out


def _parse_list(items, resolve) -> list[str]:
    out: list[str] = []
    for it in items or []:
        for part in it.split(","):
            part = part.strip()
            if part:
                out.append(resolve(part))
    return out


def _parse_weight_overrides(items) -> dict[str, float]:
    out: dict[str, float] = {}
    for it in items or []:
        for part in it.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                raise SystemExit(f"Expected NAME=VALUE for weight, got {part!r}")
            name, val = part.split("=", 1)
            key = WEIGHT_ALIASES.get(name.strip().lower())
            if key is None:
                raise SystemExit(f"Unknown weight {name!r}. Use one of: in_lane, out_of_lane, synergy, blindability")
            out[key] = float(val)
    return out


def _fmt(x, width=7):
    return ("  --  " if x is None else f"{x:.3f}").rjust(width)


# --- subcommands -----------------------------------------------------------
def cmd_build(args):
    path = build_db(force_fetch=args.force)
    with Store(path) as store:
        meta = store.meta()
    print(f"Built store: {path}")
    print(f"  cells: {meta.get('n_cells')}   playrates: {meta.get('n_playrates')}")
    print(f"  built_at: {meta.get('built_at')}")


def cmd_info(args):
    with Store() as store:
        meta = store.meta()
        weights = store.weights()
        settings = store.settings()
    print(f"Store: {config.DB_PATH}")
    for k in ("built_at", "n_cells", "n_playrates", "n_blindability", "has_blindability"):
        print(f"  {k}: {meta.get(k)}")
    print("  weights:", ", ".join(f"{k}={v}" for k, v in weights.items()))
    print("  settings:", ", ".join(f"{k}={v}" for k, v in settings.items()))


def cmd_weights(args):
    with Store() as store:
        if args.set:
            for k, v in _parse_weight_overrides(args.set).items():
                store.set_weight(k, v)
                print(f"set {k} = {v}")
        if args.agg:
            store.set_setting("agg", args.agg)
            print(f"set agg = {args.agg}")
        if args.top_n is not None:
            store.set_setting("top_n", args.top_n)
            print(f"set top_n = {args.top_n}")
        if args.rank:
            store.set_setting("default_rank", args.rank)
            print(f"set default_rank = {args.rank}")
        print("current weights:", store.weights())
        print("current settings:", store.settings())


def cmd_recommend(args):
    with Store() as store:
        resolve = _build_resolver(store)
        my_role = config.normalize_role(args.role)
        enemies = _parse_assignments(args.enemy, resolve)
        allies = _parse_assignments(args.ally, resolve)
        bans = _parse_list(args.ban, resolve)
        pool = _parse_list(args.pool, resolve) if args.pool else None
        weights = store.weights()
        weights.update(_parse_weight_overrides(args.weight))
        settings = store.settings()
        rank = args.rank or settings.get("default_rank", config.DEFAULT_RANK)

        results, warnings = score_draft(
            store, DraftState(my_role, enemies, allies, bans, pool),
            rank=rank, weights=weights, agg=args.agg, top_n=args.top_n,
        )

    # header
    agg = args.agg or settings.get("agg", config.DEFAULT_AGG)
    print(f"\nRecommendations for {my_role}   (rank: {rank}, agg: {agg})")
    wline = "  ".join(f"{COMP_LABEL[k]}={weights.get(k)}" for k in COMP_ORDER)
    print(f"weights: {wline}")
    if enemies:
        print("enemies: " + ", ".join(f"{r}={c}" for r, c in enemies.items()))
    if allies:
        print("allies:  " + ", ".join(f"{r}={c}" for r, c in allies.items()))
    if bans:
        print("bans:    " + ", ".join(bans))
    for w in warnings:
        print(f"  ! {w}")
    if not results:
        print("\nNo candidates (check role / pool / bans).")
        return

    # table
    print()
    print(f"{'#':>2}  {'Champion':<14} {'Total':>7}  "
          + " ".join(f"{COMP_LABEL[k]:>8}" for k in COMP_ORDER))
    for i, r in enumerate(results[: args.top], 1):
        comps = r.components
        cells = " ".join(_fmt(comps[k].value, 8) for k in COMP_ORDER)
        print(f"{i:>2}  {r.champion:<14} {r.total:>7.3f}  {cells}")

    if args.explain:
        print("\n--- breakdown ---")
        for i, r in enumerate(results[: args.top], 1):
            print(f"\n{i}. {r.champion}  (total {r.total:.3f})")
            for k in COMP_ORDER:
                comp = r.components[k]
                w = weights.get(k, 0.0)
                val = "--" if comp.value is None else f"{comp.value:.3f}"
                detail = ""
                if comp.contributions:
                    detail = "  [" + ", ".join(
                        f"{c.name}({c.role}) z={c.z:.2f}" for c in comp.contributions
                    ) + "]"
                elif comp.detail:
                    detail = "  " + comp.detail
                print(f"     {COMP_LABEL[k]:<9} z={val:<7} x{w:<4} = {comp.weighted:+.3f}{detail}")


def cmd_evaluate(args):
    from .evaluate import evaluate_teams
    with Store() as store:
        resolve = _build_resolver(store)
        team_a = _parse_assignments(args.team_a, resolve)
        team_b = _parse_assignments(args.team_b, resolve)
        rank = args.rank or store.settings().get("default_rank", config.DEFAULT_RANK)
        ev = evaluate_teams(store, team_a, team_b, rank=rank, weights=store.weights())
    sc = ev["score"]
    print(f"\nTeam A  {sc['a']}  —  {sc['b']}  Team B     (est. win prob A: {ev['winProbA']*100:.0f}%)")
    c = ev["components"]
    print(f"  lane edge(A) {c['laneEdge']:+.2f}pp   cross {c['crossEdge']:+.2f}pp   "
          f"synergy A/B {c['synergyA']:+.2f}/{c['synergyB']:+.2f}  (diff {c['synergyDiff']:+.2f}z)")
    print("\n  lanes:")
    for ln in ev["lanes"]:
        flag = {"A": "A>", "B": "<B", "even": "=="}[ln["favored"]]
        print(f"    {ln['role']:7} {ln['a']:13} vs {ln['b']:13} {ln['dpp']:+.2f}pp  [{flag}]")
    print("\n  win conditions:")
    for s in ev["winConditions"]:
        print(f"    - {s}")


# --- argument parsing ------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lol-draft", description="EV-based LoL draft pick recommender (machineloling z-scores)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="download + (re)build the SQLite store")
    pb.add_argument("--force", action="store_true", help="re-download even if cached")
    pb.set_defaults(func=cmd_build)

    pi = sub.add_parser("info", help="show store provenance / weights / settings")
    pi.set_defaults(func=cmd_info)

    pw = sub.add_parser("weights", help="view or edit persisted weights/settings")
    pw.add_argument("--set", action="append", metavar="NAME=VALUE",
                    help="set a weight, e.g. in_lane=0.8 (repeatable / comma-separated)")
    pw.add_argument("--agg", choices=["mean", "topn", "sum"])
    pw.add_argument("--top-n", type=int, dest="top_n")
    pw.add_argument("--rank", choices=config.RANKS)
    pw.set_defaults(func=cmd_weights)

    pr = sub.add_parser("recommend", aliases=["rec", "pick"],
                        help="recommend picks for an open role given the draft state")
    pr.add_argument("-r", "--role", required=True, help="role you are picking (TOP/JG/MID/ADC/SUP)")
    pr.add_argument("-e", "--enemy", action="append", metavar="ROLE=CHAMP",
                    help="known enemy pick (repeatable)")
    pr.add_argument("-a", "--ally", action="append", metavar="ROLE=CHAMP",
                    help="known ally pick (repeatable)")
    pr.add_argument("-b", "--ban", action="append", metavar="CHAMP",
                    help="banned champion (repeatable / comma-separated)")
    pr.add_argument("--pool", action="append", metavar="CHAMP",
                    help="restrict candidates to this pool (repeatable / comma-separated)")
    pr.add_argument("--rank", choices=config.RANKS, help="rank bracket for playrates/blindability")
    pr.add_argument("--agg", choices=["mean", "topn", "sum"], help="aggregation across opponents/allies")
    pr.add_argument("--top-n", type=int, dest="top_n", help="N for topn aggregation")
    pr.add_argument("--weight", action="append", metavar="NAME=VALUE",
                    help="override a weight for this run (repeatable / comma-separated)")
    pr.add_argument("--top", type=int, default=10, help="how many candidates to show (default 10)")
    pr.add_argument("--explain", action="store_true", help="show per-component contribution breakdown")
    pr.set_defaults(func=cmd_recommend)

    pe = sub.add_parser("evaluate", aliases=["eval", "compare"],
                        help="score a full 5v5 draft and list win conditions")
    pe.add_argument("-A", "--team-a", action="append", metavar="ROLE=CHAMP",
                    help="Team A pick, e.g. -A TOP=Aatrox (repeatable)")
    pe.add_argument("-B", "--team-b", action="append", metavar="ROLE=CHAMP",
                    help="Team B pick (repeatable)")
    pe.add_argument("--rank", choices=config.RANKS)
    pe.set_defaults(func=cmd_evaluate)

    return p


def main(argv=None):
    # Windows consoles default to cp1252; ensure non-ASCII never crashes output.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
