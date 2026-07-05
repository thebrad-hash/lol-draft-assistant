"""SQLite store: decode the static files once and persist matchup/synergy
z-scores, playrates, weights and settings for fast lookup during scoring.

Schema
------
meta(key, value)                         -- build/fetch provenance
champions(role, champion)                -- who is playable where
cells(mode, role_a, role_b, champ_a, champ_b, pp, z)
                                         -- every matchup & synergy cell
playrates(rank, role, champion, pick_rate, games, win_rate)
weights(name, value)                     -- editable scoring weights
settings(key, value)                     -- agg method, top_n, default rank
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

# NB: `decode` (which imports numpy) and `fetch` are imported lazily inside
# build_db() — so importing Store for READ access (the CLI/server/inference
# path) never pulls numpy. Keeps the Vercel serverless function lean.

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS champions (
    role TEXT, champion TEXT, PRIMARY KEY (role, champion)
);
CREATE TABLE IF NOT EXISTS cells (
    mode TEXT, role_a TEXT, role_b TEXT,
    champ_a TEXT, champ_b TEXT,
    pp REAL, z REAL,
    PRIMARY KEY (mode, role_a, role_b, champ_a, champ_b)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS playrates (
    rank TEXT, role TEXT, champion TEXT,
    pick_rate REAL, games INTEGER, win_rate REAL,
    PRIMARY KEY (rank, role, champion)
);
CREATE TABLE IF NOT EXISTS weights (name TEXT PRIMARY KEY, value REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS blindability (
    rank TEXT, role TEXT, champion TEXT,
    lane_matchup REAL, out_of_lane_matchup REAL,
    out_of_lane_synergy REAL, aggregate REAL,
    PRIMARY KEY (rank, role, champion)
);
"""


def build_db(db_path: Path | None = None, raw_dir: Path | None = None,
             *, force_fetch: bool = False) -> Path:
    """(Re)build the SQLite store from the cached/downloaded static files."""
    from . import decode, fetch  # lazy: only the (re)build path needs numpy

    db_path = db_path or config.DB_PATH
    raw_dir = raw_dir or config.RAW_DIR
    db_path.parent.mkdir(parents=True, exist_ok=True)

    paths = fetch.fetch_all(force=force_fetch, raw_dir=raw_dir)
    index = decode.load_index(paths["index.json"])
    buf = decode.load_matrices(paths["matrices.bin"])
    playrates = decode.load_playrates(paths["champions.json"])
    role_champs = decode.role_champions(index)
    blind = decode.load_blindability(raw_dir / "blindability.json")

    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)

        # champions
        con.executemany(
            "INSERT OR REPLACE INTO champions(role, champion) VALUES (?,?)",
            [(role, champ) for role, champs in role_champs.items() for champ in champs],
        )

        # cells (matchup + synergy)
        n_cells = 0
        for blk in decode.iter_blocks(buf, index):
            rows = [
                (blk.mode, blk.role_a, blk.role_b, blk.rows[i], blk.cols[j],
                 float(blk.pp[i, j]), float(blk.z[i, j]))
                for i in range(len(blk.rows))
                for j in range(len(blk.cols))
            ]
            con.executemany(
                "INSERT OR REPLACE INTO cells"
                "(mode, role_a, role_b, champ_a, champ_b, pp, z) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            n_cells += len(rows)

        # playrates
        pr_rows = [
            (rank, role, champ, d["pick_rate"], d["games"], d["win_rate"])
            for rank, by_role in playrates.items()
            for role, champs in by_role.items()
            for champ, d in champs.items()
        ]
        con.executemany(
            "INSERT OR REPLACE INTO playrates"
            "(rank, role, champion, pick_rate, games, win_rate) VALUES (?,?,?,?,?,?)",
            pr_rows,
        )

        # blindability (optional, oracle-derived)
        blind_rows = [
            (rank, role, champ, d.get("lane_matchup"), d.get("out_of_lane_matchup"),
             d.get("out_of_lane_synergy"), d.get("aggregate"))
            for rank, by_role in blind.items()
            for role, champs in by_role.items()
            for champ, d in champs.items()
        ]
        con.executemany(
            "INSERT OR REPLACE INTO blindability"
            "(rank, role, champion, lane_matchup, out_of_lane_matchup,"
            " out_of_lane_synergy, aggregate) VALUES (?,?,?,?,?,?,?)",
            blind_rows,
        )

        # weights + settings (defaults, editable later)
        con.executemany(
            "INSERT OR REPLACE INTO weights(name, value) VALUES (?,?)",
            list(config.DEFAULT_WEIGHTS.items()),
        )
        con.executemany(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)",
            [("agg", config.DEFAULT_AGG),
             ("top_n", str(config.DEFAULT_TOP_N)),
             ("default_rank", config.DEFAULT_RANK)],
        )

        # provenance
        meta = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "n_cells": str(n_cells),
            "n_playrates": str(len(pr_rows)),
            "n_blindability": str(len(blind_rows)),
            "has_blindability": "yes" if blind_rows else "no",
            "fetch_meta": json.dumps(fetch.fetch_meta(raw_dir)),
        }
        con.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", list(meta.items())
        )
        con.commit()
    finally:
        con.close()
    return db_path


class Store:
    """Read access to the built SQLite store."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.DB_PATH
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"No store at {self.db_path}. Run `python -m lol_draft.cli build` first."
            )
        # check_same_thread=False: the FastAPI server shares one Store across
        # threadpool workers; concurrent access is serialized by a lock in
        # server.py (_store_lock). Single-threaded CLI use is unaffected.
        self.con = sqlite3.connect(self.db_path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- cells ---
    def cell(self, mode: str, role_a: str, role_b: str,
             champ_a: str, champ_b: str) -> tuple[float, float] | None:
        cur = self.con.execute(
            "SELECT pp, z FROM cells WHERE mode=? AND role_a=? AND role_b=? "
            "AND champ_a=? AND champ_b=?",
            (mode, role_a, role_b, champ_a, champ_b),
        )
        row = cur.fetchone()
        return (row["pp"], row["z"]) if row else None

    def block(self, mode: str, role_a: str, role_b: str) -> dict[str, dict[str, tuple[float, float]]]:
        """Whole block as {champ_a: {champ_b: (pp, z)}} in one query."""
        cur = self.con.execute(
            "SELECT champ_a, champ_b, pp, z FROM cells "
            "WHERE mode=? AND role_a=? AND role_b=?",
            (mode, role_a, role_b),
        )
        out: dict[str, dict[str, tuple[float, float]]] = {}
        for r in cur:
            out.setdefault(r["champ_a"], {})[r["champ_b"]] = (r["pp"], r["z"])
        return out

    # --- champions ---
    def role_champions(self, role: str) -> list[str]:
        cur = self.con.execute(
            "SELECT champion FROM champions WHERE role=? ORDER BY champion", (role,)
        )
        return [r["champion"] for r in cur.fetchall()]

    def all_champions(self) -> set[str]:
        cur = self.con.execute("SELECT DISTINCT champion FROM champions")
        return {r["champion"] for r in cur.fetchall()}

    # --- playrates ---
    def pick_rate(self, rank: str, role: str, champ: str) -> float:
        cur = self.con.execute(
            "SELECT pick_rate FROM playrates WHERE rank=? AND role=? AND champion=?",
            (rank, role, champ),
        )
        row = cur.fetchone()
        return row["pick_rate"] if row else 0.0

    def pick_rates(self, rank: str, role: str) -> dict[str, float]:
        cur = self.con.execute(
            "SELECT champion, pick_rate FROM playrates WHERE rank=? AND role=?",
            (rank, role),
        )
        return {r["champion"]: r["pick_rate"] for r in cur.fetchall()}

    def win_rate(self, rank: str, role: str, champ: str) -> float | None:
        """Champion's marginal win rate at this rank/role (0..1), or None."""
        cur = self.con.execute(
            "SELECT win_rate FROM playrates WHERE rank=? AND role=? AND champion=?",
            (rank, role, champ),
        )
        row = cur.fetchone()
        return row["win_rate"] if row else None

    def win_rate_games(self, rank: str, role: str, champ: str) -> tuple[float, int] | None:
        """(win_rate, games) for the champion at this rank/role, or None. `games`
        is the sample size behind win_rate — it lets callers attach a binomial
        standard error √(wr(1-wr)/games) to champ_strength (the one feature whose
        per-champion N machineloling actually publishes)."""
        cur = self.con.execute(
            "SELECT win_rate, games FROM playrates WHERE rank=? AND role=? AND champion=?",
            (rank, role, champ),
        )
        row = cur.fetchone()
        if row is None or row["win_rate"] is None:
            return None
        return float(row["win_rate"]), int(row["games"] or 0)

    def win_rates(self, rank: str, role: str) -> dict[str, float]:
        cur = self.con.execute(
            "SELECT champion, win_rate FROM playrates WHERE rank=? AND role=?",
            (rank, role),
        )
        return {r["champion"]: r["win_rate"] for r in cur.fetchall()}

    # --- blindability (oracle-derived field-safety) ---
    def blindability(self, rank: str, role: str) -> dict[str, dict[str, float]]:
        """{champion: {lane_matchup, out_of_lane_matchup, out_of_lane_synergy,
        aggregate}} for the rank/role, or {} if not present."""
        cur = self.con.execute(
            "SELECT champion, lane_matchup, out_of_lane_matchup, "
            "out_of_lane_synergy, aggregate FROM blindability WHERE rank=? AND role=?",
            (rank, role),
        )
        return {
            r["champion"]: {
                "lane_matchup": r["lane_matchup"],
                "out_of_lane_matchup": r["out_of_lane_matchup"],
                "out_of_lane_synergy": r["out_of_lane_synergy"],
                "aggregate": r["aggregate"],
            }
            for r in cur.fetchall()
        }

    # --- weights / settings ---
    def weights(self) -> dict[str, float]:
        cur = self.con.execute("SELECT name, value FROM weights")
        return {r["name"]: r["value"] for r in cur.fetchall()}

    def set_weight(self, name: str, value: float):
        self.con.execute(
            "INSERT OR REPLACE INTO weights(name, value) VALUES (?,?)", (name, value)
        )
        self.con.commit()

    def settings(self) -> dict[str, str]:
        cur = self.con.execute("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in cur.fetchall()}

    def set_setting(self, key: str, value: str):
        self.con.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (key, str(value))
        )
        self.con.commit()

    def meta(self) -> dict[str, str]:
        cur = self.con.execute("SELECT key, value FROM meta")
        return {r["key"]: r["value"] for r in cur.fetchall()}
