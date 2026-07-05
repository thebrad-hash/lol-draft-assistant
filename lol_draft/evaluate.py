"""Team-vs-team draft evaluation ("once both teams are locked in").

Scores a full 5v5 draft using the same machineloling z-data the pick recommender
uses, and turns it into human-readable win conditions.

Everything here is derived purely from the scraped data:
  - matchup Delta-pp (signed winrate delta, pct points) for all 25 role pairings,
  - synergy z for all 10 intra-team pairings.

There is NO game-timing field in the source data, so "win early vs win late" is
expressed via the only proxy the data supports:
  - lane-matchup edge  -> the LANING phase (who wins lane = early game),
  - team synergy        -> TEAMFIGHTS / scaling (coordinated, later game).

Matchup Delta-pp is antisymmetric (A-vs-B == -(B-vs-A)), so the matchup half is
zero-sum between the teams; synergy is each team's own.
"""
from __future__ import annotations

import math

from . import config
from .store import Store

ROLES = config.ROLES  # ["TOP", "JUNGLE", "MID", "ADC", "SUP"]

# --- heuristic blend turning the raw data into one favorability number. The
# constants are tuned so a typical draft lands in a believable ~40-60% window;
# the raw components (lane pp, synergy z) are returned too, unmodified. ---
CROSS_SCALE = 0.25     # off-role (skirmish) matchups count less than the lane
PP_PER_Z = 0.8         # rough pp-equivalent of one synergy z (teamfight value)
LOGIT_SCALE = 0.12     # maps the pp-equivalent edge -> win probability

LANE_STRONG = 1.5      # |sum of lane dpp| that counts as a real laning edge
SYN_STRONG = 1.0       # |synergy z diff| that counts as a real scaling edge
LANE_NOTE = 1.0        # |dpp| for a single lane / threat worth calling out


def evaluate_teams(store: Store, team_a: dict, team_b: dict, *,
                   rank: str | None = None, weights: dict | None = None,
                   label_a: str = "Team A", label_b: str = "Team B",
                   role_label: dict | None = None) -> dict:
    """Return a JSON-serializable evaluation of team_a vs team_b.

    team_a / team_b are {role: champion} (engine role keys). Missing roles are
    skipped, so a partial draft still returns a (less confident) read.
    role_label maps engine role keys -> display names used in the prose
    (e.g. so SUP reads "Support", ADC reads "Bot").
    """
    role_label = role_label or {r: r.title() for r in ROLES}
    weights = weights or store.weights()
    w_in = weights.get("in_lane", config.DEFAULT_WEIGHTS["in_lane"])
    w_out = weights.get("out_of_lane", config.DEFAULT_WEIGHTS["out_of_lane"])
    w_syn = weights.get("synergy", config.DEFAULT_WEIGHTS["synergy"])

    roles_a = [r for r in ROLES if team_a.get(r)]
    roles_b = [r for r in ROLES if team_b.get(r)]

    # --- lane matchups (same role, head to head): the laning phase ---
    lanes = []
    lane_edge = 0.0
    lanes_with_data = 0
    for r in ROLES:
        a, b = team_a.get(r), team_b.get(r)
        if not (a and b):
            continue
        cell = store.cell("matchup", r, r, a, b)
        if cell is None:
            # Both slots ARE locked, but the source data has no head-to-head datum
            # for this pairing — typically an off-role pick machineloling doesn't
            # cover in this role's block (e.g. a jungler played bot). Show the lane
            # anyway (dropping a fully-locked lane reads as a missing role); mark it
            # no-data and let it contribute 0 to the lane edge.
            lanes.append({"role": r, "a": a, "b": b, "dpp": None,
                          "favored": "even", "noData": True})
            continue
        dpp = cell[0]
        lane_edge += dpp
        lanes_with_data += 1
        lanes.append({"role": r, "a": a, "b": b, "dpp": round(dpp, 3),
                      "favored": "A" if dpp > 0 else ("B" if dpp < 0 else "even")})

    # --- full matchup matrix (A perspective) + per-champ aggregate pressure ---
    cross_edge = 0.0
    a_pressure: dict[str, float] = {}   # how well each A champ matches the enemy team
    b_pressure: dict[str, float] = {}   # how well each B champ matches our team
    swings = []
    for ra in roles_a:
        for rb in roles_b:
            cell = store.cell("matchup", ra, rb, team_a[ra], team_b[rb])
            if cell is None:
                continue
            dpp = cell[0]
            if ra != rb:
                cross_edge += dpp
            a_pressure[team_a[ra]] = a_pressure.get(team_a[ra], 0.0) + dpp
            b_pressure[team_b[rb]] = b_pressure.get(team_b[rb], 0.0) - dpp
            swings.append({"aChamp": team_a[ra], "aRole": ra,
                           "bChamp": team_b[rb], "bRole": rb, "dpp": round(dpp, 3)})

    # --- synergy (intra-team): teamfight / scaling potential ---
    def synergy(team: dict, roles: list[str]):
        total, n, best = 0.0, 0, None
        for i, ra in enumerate(roles):
            for rb in roles[i + 1:]:
                cell = store.cell("synergy", ra, rb, team[ra], team[rb])
                if cell is None:
                    continue
                total += cell[1]
                n += 1
                if best is None or cell[1] > best["z"]:
                    best = {"a": team[ra], "b": team[rb], "z": round(cell[1], 3)}
        return total, n, best

    syn_a, na, best_a = synergy(team_a, roles_a)
    syn_b, nb, best_b = synergy(team_b, roles_b)
    syn_diff = syn_a - syn_b

    # --- composite edge -> win favorability (A perspective) ---
    edge = (w_in * lane_edge
            + w_out * CROSS_SCALE * cross_edge
            + w_syn * PP_PER_Z * syn_diff)
    win_a = 1.0 / (1.0 + math.exp(-edge * LOGIT_SCALE))
    score_a = round(win_a * 100)

    # --- standout matchups & threats ---
    swings.sort(key=lambda s: s["dpp"])
    a_worst = swings[:3]
    a_best = list(reversed(swings[-3:]))
    top_threat = max(b_pressure.items(), key=lambda kv: kv[1], default=None)
    your_carry = max(a_pressure.items(), key=lambda kv: kv[1], default=None)

    conditions = _win_conditions(
        lane_edge, syn_diff, lanes, label_a, label_b,
        best_a, best_b, top_threat, your_carry, role_label,
    )

    return {
        "complete": lanes_with_data == 5 and na == 10 and nb == 10,
        "score": {"a": score_a, "b": 100 - score_a},
        "winProbA": round(win_a, 3),
        "components": {
            "laneEdge": round(lane_edge, 3),       # A perspective (pp)
            "crossEdge": round(cross_edge, 3),     # A perspective (pp)
            "synergyA": round(syn_a, 3),
            "synergyB": round(syn_b, 3),
            "synergyDiff": round(syn_diff, 3),
        },
        "lanes": lanes,
        "synergyBest": {"a": best_a, "b": best_b},
        "swings": {"aBest": a_best, "aWorst": a_worst},
        "threats": {
            "topEnemy": ({"champ": top_threat[0], "pressure": round(top_threat[1], 3)}
                         if top_threat else None),
            "yourCarry": ({"champ": your_carry[0], "pressure": round(your_carry[1], 3)}
                          if your_carry else None),
        },
        "winConditions": conditions,
    }


def _win_conditions(lane_edge, syn_diff, lanes, la, lb,
                    best_a, best_b, top_threat, your_carry, role_label) -> list[str]:
    out: list[str] = []
    lane_team = la if lane_edge > 0 else lb
    syn_team = la if syn_diff > 0 else lb
    strong_lane = abs(lane_edge) >= LANE_STRONG
    strong_syn = abs(syn_diff) >= SYN_STRONG

    # 1) the headline early(lane) vs late(synergy) win condition
    if strong_lane and strong_syn and lane_team != syn_team:
        out.append(
            f"{lane_team} wins the laning phase (+{abs(lane_edge):.1f} lane pp) but "
            f"{syn_team} scales harder (synergy +{abs(syn_diff):.1f}z). "
            f"{lane_team} has to convert early leads before teamfights decide it; "
            f"{syn_team} wants to survive lane and group.")
    elif strong_lane and strong_syn:  # same team leads both
        out.append(
            f"{lane_team} is ahead in both lane matchups and synergy — the cleaner "
            f"draft from laning through teamfights.")
    elif strong_lane:
        out.append(
            f"{lane_team} owns the laning phase (+{abs(lane_edge):.1f} lane pp) — press "
            f"the early game; synergy is close, so don't let it stall into late teamfights.")
    elif strong_syn:
        out.append(
            f"{syn_team} has the stronger team comp (synergy +{abs(syn_diff):.1f}z) — "
            f"favored in teamfights and scaling; lanes are roughly even, so play for "
            f"objectives and grouped fights.")
    else:
        out.append(
            "Even draft — no decisive lane or synergy edge. It comes down to execution "
            "and macro, not the matchup on paper.")

    # 2) lane-by-lane callouts
    for ln in lanes:
        if ln["dpp"] is None:  # no-data lane (off-role pick) — nothing to call out
            continue
        if abs(ln["dpp"]) >= LANE_NOTE:
            winner, loser = (ln["a"], ln["b"]) if ln["dpp"] > 0 else (ln["b"], ln["a"])
            team = la if ln["dpp"] > 0 else lb
            out.append(f"{role_label.get(ln['role'], ln['role'])}: {team} favored — "
                       f"{winner} over {loser} ({abs(ln['dpp']):.1f} pp).")

    # 3) map-wide threats / carries
    if top_threat and top_threat[1] >= LANE_NOTE:
        out.append(f"Biggest enemy threat: {top_threat[0]} matches up well across the "
                   f"map (+{top_threat[1]:.1f} pp aggregate vs this comp) — track it.")
    if your_carry and your_carry[1] >= LANE_NOTE:
        out.append(f"Your strongest piece: {your_carry[0]} (+{your_carry[1]:.1f} pp into "
                   f"this enemy comp) — play through it.")

    # 4) signature synergy combos
    if best_a and best_a["z"] >= 0.5:
        out.append(f"{la} key combo: {best_a['a']} + {best_a['b']} (synergy +{best_a['z']:.1f}z).")
    if best_b and best_b["z"] >= 0.5:
        out.append(f"{lb} key combo: {best_b['a']} + {best_b['b']} (synergy +{best_b['z']:.1f}z).")

    return out
