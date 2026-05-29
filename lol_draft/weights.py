"""Context-adaptive scoring weights ("auto weights").

Fixed weights don't capture how a pick's PRIORITIES shift with the draft:

  - In-lane: you can only exploit your lane matchup once the opponent is locked;
    until then it's unknowable, so it should barely count.
  - Blindability (blind-pick safety): matters most exactly when you DON'T know
    your lane opponent (you're picking blind) and the enemy field is hidden.
    Once your opponent is locked you can counter-pick instead, so it drops hard.
  - Out-of-lane: more meaningful the more enemies are revealed.
  - Synergy: more meaningful the more allies are locked; the LAST pick is the
    keystone that ties the comp together, so synergy peaks there.

dynamic_weights() multiplies the user's base weights by context factors and
renormalizes to the same total, so emphasis shifts WITHOUT inflating EV
magnitudes (keeps cross-role "who picks next" comparisons fair). Because the
factors read the board state, they implicitly account for pick-order swaps:
change who picks when, and different things are known when you pick.
"""
from __future__ import annotations

from . import config

# --- context multiplier bounds (tunable) ---
M_IN_KNOWN, M_IN_BLIND = 1.6, 0.25            # in-lane: known opponent vs blind
M_OUT_BASE, M_OUT_SPAN = 0.6, 0.9             # out-of-lane: scales with other enemies
M_SYN_BASE, M_SYN_SPAN = 0.6, 1.1             # synergy: scales with allies locked
M_BLIND_KNOWN = 0.30                          # blind: lane opponent already known
M_BLIND_BASE, M_BLIND_SPAN = 0.8, 0.9         # blind: scales with hidden enemy field

KEYS = ("in_lane", "out_of_lane", "synergy", "blindability")


def dynamic_weights(base: dict, my_role: str, enemies: dict, allies: dict) -> tuple[dict, list[str]]:
    """Return (adjusted weights, human-readable notes).

    base: {in_lane, out_of_lane, synergy, blindability}.
    enemies / allies: {engine-role: champion} of what's locked so far.
    """
    n_roles = len(config.ROLES)
    n_enemy = sum(1 for r in config.ROLES if enemies.get(r))
    n_ally = sum(1 for r in config.ROLES if r != my_role and allies.get(r))
    dir_known = bool(enemies.get(my_role))
    n_other = n_enemy - (1 if dir_known else 0)
    enemy_unknown = (n_roles - n_enemy) / n_roles  # 0..1

    mult = {
        "in_lane": M_IN_KNOWN if dir_known else M_IN_BLIND,
        "out_of_lane": M_OUT_BASE + M_OUT_SPAN * (n_other / 4.0),
        "synergy": M_SYN_BASE + M_SYN_SPAN * (n_ally / 4.0),
        "blindability": M_BLIND_KNOWN if dir_known else (M_BLIND_BASE + M_BLIND_SPAN * enemy_unknown),
    }

    raw = {k: max(0.0, base.get(k, 0.0)) * mult[k] for k in KEYS}
    base_sum = sum(max(0.0, base.get(k, 0.0)) for k in KEYS) or 1.0
    raw_sum = sum(raw.values()) or 1.0
    weights = {k: round(raw[k] * base_sum / raw_sum, 3) for k in KEYS}

    return weights, _notes(dir_known, n_ally, n_enemy, enemies, my_role)


def _notes(dir_known, n_ally, n_enemy, enemies, my_role) -> list[str]:
    notes = [f"Pick {n_ally + 1} of 5 · {n_enemy}/5 enemies revealed"]
    if dir_known:
        notes.append(f"Lane opponent locked ({enemies.get(my_role)}) → in-lane ↑, blind ↓")
    else:
        notes.append("Lane opponent unknown → blind-pick safety ↑, in-lane ↓")
    if n_ally >= 4:
        notes.append("Last pick → synergy ↑↑ (your team's keystone)")
    elif n_ally >= 2:
        notes.append("Several allies locked → synergy ↑")
    return notes
