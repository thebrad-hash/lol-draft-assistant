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


def test_live_mapping() -> bool:
    """LCU champ-select -> UI draft: the enemy team has NO assignedPosition (the
    client always hides it), so without role inference every enemy pick is
    dropped. Verify inference maps them, the local role resolves, and all bans
    (a full draft has 10) are collected. Pure mapping test: no store needed."""
    from lol_draft.lcu import session_to_draft
    print("\n[live] champ-select mapping (hidden enemy positions + 10 bans)")
    cmap = {1: "Sejuani", 2: "Vayne", 3: "Milio", 4: "Udyr",
            5: "Sylas", 6: "Pantheon", 7: "Fiora", 8: "Shyvana"}
    ban_ids = list(range(20, 30))
    for i, cid in enumerate(ban_ids):
        cmap[cid] = f"Ban{i}"
    champ_roles = {
        "Sylas": ["MID", "TOP"], "Pantheon": ["SUPPORT", "TOP", "MID"],
        "Fiora": ["TOP"], "Shyvana": ["JUNGLE", "TOP"],
    }
    session = {
        "localPlayerCellId": 2,
        "myTeam": [
            {"cellId": 1, "championId": 1, "assignedPosition": "jungle"},
            {"cellId": 2, "championId": 2, "assignedPosition": "bottom"},
            {"cellId": 3, "championId": 3, "assignedPosition": "utility"},
            {"cellId": 4, "championId": 4, "assignedPosition": "top"},
            {"cellId": 5, "championId": 0, "assignedPosition": "middle"},
        ],
        "theirTeam": [  # champion known, assignedPosition hidden -> must infer
            {"cellId": 6, "championId": 5, "assignedPosition": ""},
            {"cellId": 7, "championId": 6, "assignedPosition": ""},
            {"cellId": 8, "championId": 7, "assignedPosition": ""},
            {"cellId": 9, "championId": 8, "assignedPosition": ""},
            {"cellId": 10, "championId": 0, "assignedPosition": ""},
        ],
        "actions": [[{"type": "ban", "completed": True, "championId": c} for c in ban_ids]],
        "bans": {},
    }
    d = session_to_draft(session, cmap, champ_roles)
    ok = True
    if d["myTeam"] != {"JUNGLE": "Sejuani", "BOT": "Vayne", "SUPPORT": "Milio", "TOP": "Udyr"}:
        print(f"  BAD myTeam={d['myTeam']}"); ok = False
    if d["pickingForRole"] != "BOT":
        print(f"  BAD pickingForRole={d['pickingForRole']} (want BOT)"); ok = False
    if set(d["enemyTeam"].values()) != {"Sylas", "Pantheon", "Fiora", "Shyvana"}:
        print(f"  BAD enemyTeam={d['enemyTeam']}"); ok = False
    if len(d["bans"]) != 10:
        print(f"  BAD bans={len(d['bans'])} (want 10)"); ok = False
    if ok:
        print(f"  ok  enemy inferred -> {d['enemyTeam']}")
        print(f"      bans={len(d['bans'])}  picking={d['pickingForRole']}")
    return ok


def test_team_eval(store: Store) -> bool:
    """Full 5v5 evaluation: scores sum to 100, all five lanes resolved, win
    conditions generated, and swapping the teams mirrors the score."""
    from lol_draft.evaluate import evaluate_teams
    print("\n[evaluate] full 5v5 scoring + win conditions")
    A = {"TOP": "Aatrox", "JUNGLE": "LeeSin", "MID": "Ahri", "ADC": "Jinx", "SUP": "Thresh"}
    B = {"TOP": "Darius", "JUNGLE": "Sejuani", "MID": "Zed", "ADC": "Caitlyn", "SUP": "Lulu"}
    ev = evaluate_teams(store, A, B)
    ok = True
    if not ev["complete"]:
        print("  BAD eval not marked complete for full teams"); ok = False
    if ev["score"]["a"] + ev["score"]["b"] != 100:
        print(f"  BAD scores don't sum to 100: {ev['score']}"); ok = False
    if len(ev["lanes"]) != 5:
        print(f"  BAD expected 5 lanes, got {len(ev['lanes'])}"); ok = False
    if not ev["winConditions"]:
        print("  BAD no win conditions generated"); ok = False
    # swapping teams should mirror the score (allow +/-1 for rounding)
    ev2 = evaluate_teams(store, B, A)
    if abs(ev2["score"]["a"] - ev["score"]["b"]) > 1:
        print(f"  BAD not symmetric: AvB.b={ev['score']['b']} BvA.a={ev2['score']['a']}"); ok = False
    if ok:
        print(f"  ok  score {ev['score']['a']}-{ev['score']['b']}  "
              f"laneEdge {ev['components']['laneEdge']:+.2f}  "
              f"synDiff {ev['components']['synergyDiff']:+.2f}  "
              f"{len(ev['winConditions'])} conditions")
    return ok


def test_dynamic_weights() -> bool:
    """Context-adaptive weights: blind > in-lane when the lane opponent is hidden,
    in-lane > blind once it's known, synergy peaks on the last pick, total preserved."""
    from lol_draft.weights import dynamic_weights
    print("\n[weights] context-adaptive scoring weights")
    base = {"in_lane": 0.7, "out_of_lane": 0.5, "synergy": 1.0, "blindability": 0.9}
    base_sum = sum(base.values())
    ok = True

    w_blind, _ = dynamic_weights(base, "ADC",
                                 {"JUNGLE": "Viego", "SUP": "Zilean"},
                                 {"JUNGLE": "LeeSin", "SUP": "Karma"})
    if not w_blind["blindability"] > w_blind["in_lane"]:
        print(f"  BAD blind: blind {w_blind['blindability']} !> in {w_blind['in_lane']}"); ok = False

    w_known, _ = dynamic_weights(base, "ADC",
                                 {"ADC": "Caitlyn", "JUNGLE": "Viego", "SUP": "Zilean"},
                                 {"JUNGLE": "LeeSin", "SUP": "Karma"})
    if not w_known["in_lane"] > w_known["blindability"]:
        print(f"  BAD known: in {w_known['in_lane']} !> blind {w_known['blindability']}"); ok = False

    w_last, _ = dynamic_weights(base, "ADC",
                                {"TOP": "Aatrox", "JUNGLE": "Viego", "MID": "Ahri", "ADC": "Caitlyn", "SUP": "Zilean"},
                                {"TOP": "Garen", "JUNGLE": "LeeSin", "MID": "Sylas", "SUP": "Karma"})
    if w_last["synergy"] != max(w_last.values()):
        print(f"  BAD last-pick: synergy not dominant in {w_last}"); ok = False

    for label, w in (("blind", w_blind), ("known", w_known), ("last", w_last)):
        if abs(sum(w.values()) - base_sum) > 0.05:
            print(f"  BAD {label} sum {sum(w.values()):.2f} != base {base_sum:.2f}"); ok = False

    if ok:
        print(f"  ok  blind: blind={w_blind['blindability']} in={w_blind['in_lane']} | "
              f"known: in={w_known['in_lane']} blind={w_known['blindability']} | "
              f"last: syn={w_last['synergy']}")
    return ok


def main() -> int:
    with Store() as store:
        results = [
            test_parity(store),
            test_scoring(store),
            test_partial_no_penalty(store),
            test_team_eval(store),
        ]
    results.append(test_live_mapping())
    results.append(test_dynamic_weights())
    passed = all(results)
    print("\n==== " + ("ALL TESTS PASSED" if passed else "SOME TESTS FAILED") + " ====")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
