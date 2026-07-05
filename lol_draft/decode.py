"""Decode machineloling's static data files into matchup/synergy z-scores.

Binary layout of matrices.bin (reverse-engineered and validated cell-for-cell
against the site's own WASM engine, errors < 0.01 = f16 rounding only):

Each block described by index.json[mode][role_a][role_b] = {offset, rows_n,
cols_n, rows[], cols[]}. With r = rows_n, c = cols_n, the block occupies
`(r+c)*2 + r*c*4` bytes laid out PLANAR with the header at the END:

    [offset            , offset + r*c*2 ) : channel A  (r x c f16, row-major)  -- shrunk Δpp variant, unused
    [offset + r*c*2    , offset + 2*r*c*2): channel B  (r x c f16, row-major)  -- Δpp (signed winrate delta, pct points)
    [offset + 2*r*c*2  , offset + blockSz): header     ((r+c) f16)             -- row then col aggregates (pick-rate-ish)

All values are IEEE half-precision (float16), little-endian.

The z-score the site uses for scoring is DERIVED, not stored:

    z[i][j] = (pp[i][j] - colMean[j]) / colStd[j]

where colMean/colStd are POPULATION statistics over every row of column j in
that block (i.e. each opponent/ally column is normalized across all candidate
champions of the row-role). row i = champion `rows[i]`, col j = `cols[j]`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Bump when the decoding math changes (layout, z derivation). Recorded in every
# snapshot manifest so a cross-snapshot audit knows whether the two sides were
# decoded the same way.
DECODER_VERSION = 1


@dataclass
class Block:
    mode: str           # "matchup" | "synergy"
    role_a: str         # row role (the candidate's role)
    role_b: str         # column role (opponent/ally role)
    rows: list[str]     # champion name per row
    cols: list[str]     # champion name per col
    pp: np.ndarray      # (rows_n, cols_n) float32 — Δpp
    z: np.ndarray       # (rows_n, cols_n) float32 — column-normalized z

    def cell(self, row_champ: str, col_champ: str) -> tuple[float, float] | None:
        """Return (pp, z) for row_champ vs/with col_champ, or None if absent."""
        try:
            i = self.rows.index(row_champ)
            j = self.cols.index(col_champ)
        except ValueError:
            return None
        return float(self.pp[i, j]), float(self.z[i, j])


def load_index(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _decode_pp(buf: bytes, offset: int, rows_n: int, cols_n: int) -> np.ndarray:
    """Decode channel B (Δpp) as a row-major rows_n x cols_n float32 matrix."""
    base = offset + rows_n * cols_n * 2  # skip channel A
    flat = np.frombuffer(buf, dtype="<f2", count=rows_n * cols_n, offset=base)
    return flat.astype(np.float32).reshape(rows_n, cols_n)


def _column_z(pp: np.ndarray) -> np.ndarray:
    """z = (pp - colMean) / colStd, population stats over rows (axis 0)."""
    mean = pp.mean(axis=0, keepdims=True)
    std = pp.std(axis=0, keepdims=True)  # numpy default ddof=0 == population
    safe = np.where(std > 0, std, 1.0)
    return (pp - mean) / safe


def decode_block(buf: bytes, index: dict, mode: str, role_a: str, role_b: str) -> Block:
    meta = index[mode][role_a][role_b]
    pp = _decode_pp(buf, meta["offset"], meta["rows_n"], meta["cols_n"])
    z = _column_z(pp)
    return Block(mode, role_a, role_b, meta["rows"], meta["cols"], pp, z)


def iter_blocks(buf: bytes, index: dict):
    """Yield every matchup and synergy Block present in the index."""
    for mode in ("matchup", "synergy"):
        for role_a, inner in index.get(mode, {}).items():
            for role_b in inner:
                yield decode_block(buf, index, mode, role_a, role_b)


def load_matrices(bin_path: str | Path) -> bytes:
    return Path(bin_path).read_bytes()


# --- champions.json: per-rank, per-role playrates ---
def load_playrates(path: str | Path) -> dict:
    """Return {rank: {role: {champion: {pick_rate, games, win_rate}}}}."""
    with open(path, "r", encoding="utf-8") as f:
        ch = json.load(f)
    out: dict = {}
    for rank, by_role in ch.get("by_patch", {}).items():
        out[rank] = {}
        for role, entries in by_role.items():
            out[rank][role] = {
                e["champion"]: {
                    "pick_rate": e.get("pick_rate", 0.0),
                    "games": e.get("games", 0),
                    "win_rate": e.get("win_rate", 0.0),
                }
                for e in entries
            }
    return out


def role_champions(index: dict) -> dict[str, list[str]]:
    """{role: [champion, ...]} — the champions playable in each role."""
    return dict(index.get("champions", {}))


def load_blindability(path: str | Path) -> dict:
    """Optional oracle-derived blind-pick safety table.

    Returns {rank: {role: {champion: {lane_matchup, out_of_lane_matchup,
    out_of_lane_synergy, aggregate}}}} or {} if the file is absent.
    """
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    out: dict = {}
    for rank, by_role in data.items():
        out[rank] = {}
        for role, rows in by_role.items():
            out[rank][role] = {r["champion"]: r for r in rows}
    return out
