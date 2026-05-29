"""End-to-end smoke + parity test (no pytest needed).

Run:  python tests/smoke_test.py    (from the project root, after `build`)

Checks:
1. Decode/store parity: a handful of cells match machineloling's WASM-oracle
   ground-truth values (pp within 0.02, z within 0.02 = f16 rounding).
2. Scoring sanity: a sample partial draft produces ranked candidates with the
   expected component structure and no exceptions.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lol_draft.scoring import DraftState, score_draft  # noqa: E402
from lol_draft.store import Store  # noqa: E402

# (mode, role_a, role_b, champ_a, champ_b, expected_pp, expected_z) from the oracle
ORACLE = [
    ("matchup", "TOP", "TOP", "Aatrox", "DrMundo", 5.156, 1.666),
    ("matchup", "TOP", "TOP", "Aatrox", "Darius", 0.095, 0.107),
    ("matchup", "TOP", "TOP", "Aatrox", "Fiora", -1.815, -0.714),
    ("matchup", "TOP", "JUNGLE", "Aatrox", "LeeSin", -0.602, -0.858),
    ("synergy", "ADC", "SUP", "Jinx", "Lulu", 0.293, 0.703),
    ("matchup", "JUNGLE", "TOP", "LeeSin", "Garen", 1.016, 0.818),
]


def test_parity(store: Store) -> bool:
    print("[parity] Python store cells vs WASM-oracle ground truth")
    ok = True
    for mode, ra, rb, a, b, epp, ez in ORACLE:
        cell = store.cell(mode, ra, rb, a, b)
        if cell is None:
            print(f"  MISS {mode} {ra}->{rb} {a} v {b}")
            ok = False
            continue
        pp, z = cell
        dpp, dz = abs(pp - epp), abs(z - ez)
        flag = "ok " if (dpp < 0.02 and dz < 0.02) else "BAD"
        if flag == "BAD":
            ok = False
        print(f"  {flag} {mode} {ra}->{rb} {a:<8} v {b:<8} "
              f"pp={pp:7.3f}(d{dpp:.3f})  z={z:7.3f}(d{dz:.3f})")
    return ok


def test_scoring(store: Store) -> bool:
    print("\n[scoring] sample partial draft: TOP vs Darius/LeeSin/Ahri + Jinx/Thresh")
    state = DraftState(
        my_role="TOP",
        enemies={"TOP": "Darius", "JUNGLE": "LeeSin", "MID": "Ahri"},
        allies={"ADC": "Jinx", "SUP": "Thresh"},
        bans=["Garen"],
    )
    results, warnings = score_draft(store, state)
    ok = True
    if not results:
        print("  BAD no results")
        return False
    # bans / picked champs excluded
    names = {r.champion for r in results}
    for excluded in ("Garen", "Darius"):
        if excluded in names:
            print(f"  BAD {excluded} should be excluded")
            ok = False
    # structure: every candidate has all four components; total == weighted sum
    for r in results[:5]:
        assert set(r.components) == {"in_lane", "out_of_lane", "synergy", "blindability"}
        recomputed = sum(c.weighted for c in r.components.values())
        if abs(recomputed - r.total) > 1e-9:
            print(f"  BAD {r.champion} total mismatch {r.total} vs {recomputed}")
            ok = False
    # sorted descending
    totals = [r.total for r in results]
    if totals != sorted(totals, reverse=True):
        print("  BAD results not sorted desc")
        ok = False
    print(f"  ok  {len(results)} candidates, top: "
          + ", ".join(f"{r.champion}({r.total:.2f})" for r in results[:5]))
    for w in warnings:
        print(f"  note: {w}")
    return ok


def test_partial_no_penalty(store: Store) -> bool:
    print("\n[scoring] partial draft (only one off-role enemy) yields absent components")
    state = DraftState(my_role="MID", enemies={"ADC": "Jinx"})
    results, _ = score_draft(store, state)
    ok = bool(results)
    top = results[0]
    if top.components["in_lane"].value is not None:
        print("  BAD in_lane should be None (no MID enemy)")
        ok = False
    if top.components["synergy"].value is not None:
        print("  BAD synergy should be None (no allies)")
        ok = False
    if top.components["out_of_lane"].value is None:
        print("  BAD out_of_lane should be present (ADC enemy known)")
        ok = False
    print(f"  ok  top={top.champion} in_lane={top.components['in_lane'].value} "
          f"out={top.components['out_of_lane'].value:.3f}")
    return ok


def main() -> int:
    with Store() as store:
        results = [
            test_parity(store),
            test_scoring(store),
            test_partial_no_penalty(store),
        ]
    passed = all(results)
    print("\n==== " + ("ALL TESTS PASSED" if passed else "SOME TESTS FAILED") + " ====")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
