"""Snapshot archive + cross-snapshot stationarity audit (WS1).

machineloling republishes its static files each patch with no version field, so
by default a re-fetch silently overwrites the previous data. This module

  1. ARCHIVES every fetched snapshot under data/snapshots/<patch>_<date>/ with a
     manifest (patch stamp from Riot's live-version list at fetch time, SHA-256
     per file, decoder version) — idempotent, keyed on content hashes; and
  2. AUDITS two snapshots against each other: per role-pair correlation of the
     matchup/synergy z matrices, per-champion break scores (rework detector),
     and the per-cell z values from both sides (the raw material WS3 uses to
     calibrate its cell-noise constant).

The audit is an ALERT, never a gate — a low correlation warns, nothing blocks.
Archives are inputs to the audit and to WS3 calibration; nothing here runs at
serve time, so this module uses its own file access (no _store_lock).

CLI: `python -m lol_draft.cli snapshot-audit [--a DIR --b DIR]` (default: newest
vs previous). `python -m lol_draft.snapshot --selftest` proves archive+audit on
a synthetic two-snapshot fixture, offline.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import config

# Audit thresholds (configurable via CLI flags; these are the defaults).
WARN_CORRELATION = 0.90   # warn if any role-pair matrix correlates below this
TOP_MOVERS = 15           # per-champion break scores reported

MANIFEST_NAME = "manifest.json"
# blindability.json is oracle-derived from the same fetch; archive it when
# present so a snapshot is a complete decode input, but never require it.
OPTIONAL_FILES = ["blindability.json"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_sidecar(raw_dir: Path, name: str) -> dict:
    p = raw_dir / f"{name}.meta.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


# --- archive ---------------------------------------------------------------
def list_snapshots(snapshots_dir: Path | None = None) -> list[tuple[Path, dict]]:
    """[(snapshot dir, manifest)] sorted oldest -> newest by fetched_at."""
    snapshots_dir = snapshots_dir or config.SNAPSHOTS_DIR
    out: list[tuple[Path, dict]] = []
    if not snapshots_dir.is_dir():
        return out
    for d in snapshots_dir.iterdir():
        mp = d / MANIFEST_NAME
        if d.is_dir() and mp.exists():
            try:
                out.append((d, json.loads(mp.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError):
                continue
    out.sort(key=lambda t: t[1].get("fetched_at") or "")
    return out


def archive_snapshot(raw_dir: Path | None = None,
                     snapshots_dir: Path | None = None,
                     *, quiet: bool = False) -> Path | None:
    """Archive the current data/raw/ contents as a patch-stamped snapshot.

    Idempotent on CONTENT: if any existing snapshot holds byte-identical source
    files (same SHA-256 set), nothing is written and that snapshot's dir is
    returned. Missing required files -> warn and return None (never raises: the
    archive step must not be able to break a build)."""
    raw_dir = raw_dir or config.RAW_DIR
    snapshots_dir = snapshots_dir or config.SNAPSHOTS_DIR
    try:
        missing = [n for n in config.DATA_FILES if not (raw_dir / n).exists()]
        if missing:
            if not quiet:
                print(f"  ! snapshot archive skipped: missing {missing} in {raw_dir}")
            return None

        names = list(config.DATA_FILES) + [n for n in OPTIONAL_FILES
                                           if (raw_dir / n).exists()]
        hashes = {n: _sha256(raw_dir / n) for n in names}
        # Idempotency: identical content anywhere -> skip. (Content, not patch
        # label: the site lagging a patch rollover produces identical bytes.)
        for d, manifest in list_snapshots(snapshots_dir):
            existing = {f["name"]: f["sha256"] for f in manifest.get("files", [])}
            if existing == hashes:
                if not quiet:
                    print(f"  snapshot archive: identical to {d.name}, skipped")
                return d

        # Patch stamp: the fetch sidecar's riot_patch (recorded at download
        # time). The pre-sidecar legacy cache has none -> "unknown".
        sidecars = {n: _read_sidecar(raw_dir, n) for n in names}
        patch = next((s.get("riot_patch") for s in sidecars.values()
                      if s.get("riot_patch")), None) or "unknown"
        fetched_at = min((s.get("fetched_at") for s in sidecars.values()
                          if s.get("fetched_at")), default=None)
        if fetched_at is None:  # legacy cache without sidecars: use file mtime
            mtime = min((raw_dir / n).stat().st_mtime for n in names)
            fetched_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

        base = f"{patch}_{fetched_at[:10]}"
        dest = snapshots_dir / base
        n = 2
        while dest.exists():  # same patch+date, different content
            dest = snapshots_dir / f"{base}-{n}"
            n += 1
        dest.mkdir(parents=True)
        for name in names:
            shutil.copy2(raw_dir / name, dest / name)
        from .decode import DECODER_VERSION  # lazy: keep archive numpy-free
        manifest = {
            "patch": patch,
            "fetched_at": fetched_at,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "source": config.DATA_BASE_URL,
            "decoder_version": DECODER_VERSION,
            "files": [{"name": n_, "sha256": hashes[n_],
                       "bytes": (raw_dir / n_).stat().st_size} for n_ in names],
        }
        (dest / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2),
                                          encoding="utf-8")
        if not quiet:
            print(f"  snapshot archived: {dest.name} (patch {patch})")
        return dest
    except Exception as e:  # the archive must never break a build
        if not quiet:
            print(f"  ! snapshot archive failed (build unaffected): {e}")
        return None


# --- audit -------------------------------------------------------------------
def _avg_ranks(x):
    """Average ranks (1-based, ties averaged) — Spearman without scipy."""
    import numpy as np
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    _, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inv, ranks)
    return (sums / counts)[inv]


def _corr(a, b) -> float | None:
    import numpy as np
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _decode_snapshot(snap_dir: Path):
    from . import decode
    index = decode.load_index(snap_dir / "index.json")
    buf = decode.load_matrices(snap_dir / "matrices.bin")
    return index, buf


def audit_snapshots(dir_a: Path, dir_b: Path, *,
                    warn_corr: float = WARN_CORRELATION,
                    top_movers: int = TOP_MOVERS,
                    reports_dir: Path | None = None) -> tuple[dict, Path, Path]:
    """Compare snapshot A (older) vs B (newer). Returns (report, report_path,
    cells_path). Decodes both in memory; nothing touches draft.db.

    Per role-pair (over cells present in BOTH snapshots): Pearson + Spearman of
    the z values. Per champion: mean |Δz| over that champion's matchup and
    synergy ROWS (unweighted until the WS3 cell-N proxy lands). The per-cell z
    pairs are persisted gzipped next to the report — WS3's calibration of the
    cell-noise constant regresses on exactly those values."""
    import numpy as np
    from . import decode

    reports_dir = reports_dir or config.REPORTS_DIR
    index_a, buf_a = _decode_snapshot(dir_a)
    index_b, buf_b = _decode_snapshot(dir_b)

    blocks = []
    movers: dict[str, dict] = {}   # champion -> {sum, n, per-mode sums}
    cells = {k: [] for k in ("mode", "role_a", "role_b", "champ_a", "champ_b",
                             "z_a", "z_b")}

    for mode in ("matchup", "synergy"):
        for role_a, inner in index_a.get(mode, {}).items():
            for role_b in inner:
                if role_b not in index_b.get(mode, {}).get(role_a, {}):
                    continue
                blk_a = decode.decode_block(buf_a, index_a, mode, role_a, role_b)
                blk_b = decode.decode_block(buf_b, index_b, mode, role_a, role_b)
                col_b = {c: j for j, c in enumerate(blk_b.cols)}
                row_b = {r: i for i, r in enumerate(blk_b.rows)}
                za, zb, rows_of = [], [], []
                for i, ra_champ in enumerate(blk_a.rows):
                    ib = row_b.get(ra_champ)
                    if ib is None:
                        continue
                    for j, cb_champ in enumerate(blk_a.cols):
                        jb = col_b.get(cb_champ)
                        if jb is None:
                            continue
                        za.append(float(blk_a.z[i, j]))
                        zb.append(float(blk_b.z[ib, jb]))
                        rows_of.append(ra_champ)
                        cells["mode"].append(mode)
                        cells["role_a"].append(role_a)
                        cells["role_b"].append(role_b)
                        cells["champ_a"].append(ra_champ)
                        cells["champ_b"].append(cb_champ)
                        cells["z_a"].append(round(za[-1], 5))
                        cells["z_b"].append(round(zb[-1], 5))
                za_arr, zb_arr = np.asarray(za), np.asarray(zb)
                pearson = _corr(za_arr, zb_arr)
                spearman = (_corr(_avg_ranks(za_arr), _avg_ranks(zb_arr))
                            if pearson is not None else None)
                blocks.append({
                    "mode": mode, "role_a": role_a, "role_b": role_b,
                    "n_shared": len(za),
                    "pearson": None if pearson is None else round(pearson, 4),
                    "spearman": None if spearman is None else round(spearman, 4),
                    "warn": pearson is not None and pearson < warn_corr,
                })
                # per-champion break accumulation (row champion)
                abs_dz = np.abs(za_arr - zb_arr)
                for champ, d in zip(rows_of, abs_dz):
                    m = movers.setdefault(champ, {"sum": 0.0, "n": 0,
                                                  "matchup_sum": 0.0, "matchup_n": 0,
                                                  "synergy_sum": 0.0, "synergy_n": 0})
                    m["sum"] += float(d)
                    m["n"] += 1
                    m[f"{mode}_sum"] += float(d)
                    m[f"{mode}_n"] += 1

    mover_rows = sorted(
        ({"champion": c,
          "mean_abs_dz": round(m["sum"] / m["n"], 4),
          "n_cells": m["n"],
          "matchup_mean_abs_dz": round(m["matchup_sum"] / m["matchup_n"], 4)
              if m["matchup_n"] else None,
          "synergy_mean_abs_dz": round(m["synergy_sum"] / m["synergy_n"], 4)
              if m["synergy_n"] else None}
         for c, m in movers.items() if m["n"] > 0),
        key=lambda r: r["mean_abs_dz"], reverse=True)

    pearsons = [b["pearson"] for b in blocks if b["pearson"] is not None]
    report = {
        "snapshot_a": dir_a.name, "snapshot_b": dir_b.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warn_correlation_threshold": warn_corr,
        "n_blocks": len(blocks),
        "n_shared_cells": len(cells["z_a"]),
        "min_pearson": min(pearsons) if pearsons else None,
        "median_pearson": (round(float(sorted(pearsons)[len(pearsons) // 2]), 4)
                           if pearsons else None),
        "n_blocks_below_threshold": sum(1 for b in blocks if b["warn"]),
        "blocks": blocks,
        "top_movers": mover_rows[:top_movers],
        "cells_file": None,  # filled below
        "weighting": "unweighted (WS3 cell-N proxy not yet available)",
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"snapshot_audit_{dir_a.name}_vs_{dir_b.name}"
    cells_path = reports_dir / f"{stem}_cells.json.gz"
    with gzip.open(cells_path, "wt", encoding="utf-8") as f:
        json.dump({"format": "parallel-arrays",
                   "snapshot_a": dir_a.name, "snapshot_b": dir_b.name,
                   "cells": cells}, f)
    report["cells_file"] = cells_path.name
    report_path = reports_dir / f"{stem}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report, report_path, cells_path


def print_summary(report: dict) -> None:
    """Human-readable audit summary (the report JSON is the machine artifact)."""
    print(f"=== SNAPSHOT AUDIT: {report['snapshot_a']}  vs  {report['snapshot_b']} ===")
    print(f"  {report['n_blocks']} role-pair blocks, "
          f"{report['n_shared_cells']} shared cells")
    print(f"  z-matrix correlation: min pearson {report['min_pearson']}, "
          f"median {report['median_pearson']}")
    nb = report["n_blocks_below_threshold"]
    thr = report["warn_correlation_threshold"]
    if nb:
        print(f"  ! WARNING: {nb} block(s) correlate below {thr}:")
        for b in report["blocks"]:
            if b["warn"]:
                print(f"      {b['mode']:8} {b['role_a']:>6} vs {b['role_b']:<6} "
                      f"pearson={b['pearson']}  spearman={b['spearman']}  "
                      f"(n={b['n_shared']})")
    else:
        print(f"  all blocks correlate >= {thr} — interaction z's look stable")
    print(f"  top movers (mean |dz| over the champion's rows, "
          f"{report['weighting']}):")
    for i, m in enumerate(report["top_movers"], 1):
        print(f"    {i:>2}. {m['champion']:<14} {m['mean_abs_dz']:.3f}  "
              f"(matchup {m['matchup_mean_abs_dz']}, synergy "
              f"{m['synergy_mean_abs_dz']}, n={m['n_cells']})")
    print("  (alert only — the audit never blocks a build)")


def run_audit_cli(a: str | None, b: str | None, *, warn_corr: float,
                  top: int, snapshots_dir: Path | None = None) -> int:
    """The `snapshot-audit` CLI body. Returns an exit code; degrades gracefully
    when fewer than two snapshots exist (that's exit 0 — absence is not an error)."""
    snapshots_dir = snapshots_dir or config.SNAPSHOTS_DIR

    def resolve(token: str) -> Path | None:
        p = Path(token)
        if p.is_dir() and (p / MANIFEST_NAME).exists():
            return p
        p = snapshots_dir / token
        if p.is_dir() and (p / MANIFEST_NAME).exists():
            return p
        return None

    if a or b:
        if not (a and b):
            print("Provide both --a and --b (or neither for newest-vs-previous).")
            return 2
        da, db = resolve(a), resolve(b)
        if da is None or db is None:
            print(f"Snapshot not found: {a if da is None else b} "
                  f"(looked in {snapshots_dir})")
            return 2
    else:
        snaps = list_snapshots(snapshots_dir)
        if len(snaps) < 2:
            have = ", ".join(d.name for d, _ in snaps) or "none"
            print(f"Need two snapshots to audit; found {len(snaps)} ({have}).")
            print("A new snapshot is archived automatically whenever `build` "
                  "fetches fresh data (`python -m lol_draft.cli build --force`).")
            return 0
        da, db = snaps[-2][0], snaps[-1][0]

    report, report_path, cells_path = audit_snapshots(
        da, db, warn_corr=warn_corr, top_movers=top)
    print_summary(report)
    print(f"\n  report: {report_path}")
    print(f"  cells:  {cells_path}")
    return 0


# --- offline self-test (synthetic two-snapshot fixture; no network) ----------
def _write_synthetic_snapshot(dest: Path, z_shift, *, patch: str,
                              fetched_at: str) -> None:
    """A minimal valid snapshot: 2 roles, 3 champs/role, matchup + synergy
    blocks in the real binary layout (channel A + channel B + trailing header,
    all f16 planar). `z_shift(mode, role_a, role_b, i, j)` perturbs channel B."""
    import numpy as np

    roles = ["TOP", "MID"]
    champs = {"TOP": ["Aatrox", "Darius", "Garen"],
              "MID": ["Ahri", "Zed", "Viktor"]}
    rng = np.random.default_rng(7)  # same base data for both snapshots

    index: dict = {"champions": champs, "matchup": {}, "synergy": {}}
    payload = bytearray()
    base_pp = {}
    for mode in ("matchup", "synergy"):
        for ra in roles:
            for rb in roles:
                base_pp[(mode, ra, rb)] = rng.normal(0.0, 2.0, size=(3, 3))
    for mode in ("matchup", "synergy"):
        index[mode] = {}
        for ra in roles:
            index[mode][ra] = {}
            for rb in roles:
                rows, cols = champs[ra], champs[rb]
                pp = base_pp[(mode, ra, rb)].copy()
                for i in range(3):
                    for j in range(3):
                        pp[i, j] += z_shift(mode, ra, rb, rows[i], cols[j])
                offset = len(payload)
                chan_a = np.zeros((3, 3), dtype="<f2")   # unused shrunk variant
                chan_b = pp.astype("<f2")
                header = np.zeros(6, dtype="<f2")        # row+col aggregates
                payload += chan_a.tobytes() + chan_b.tobytes() + header.tobytes()
                index[mode][ra][rb] = {"offset": offset, "rows_n": 3, "cols_n": 3,
                                       "rows": rows, "cols": cols}

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "matrices.bin").write_bytes(bytes(payload))
    (dest / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (dest / "champions.json").write_text(json.dumps({"by_patch": {}}),
                                         encoding="utf-8")
    for name in config.DATA_FILES:
        (dest / f"{name}.meta.json").write_text(json.dumps({
            "url": f"synthetic/{name}", "fetched_at": fetched_at,
            "bytes": (dest / name).stat().st_size, "riot_patch": patch,
        }), encoding="utf-8")


def _selftest() -> bool:
    """Archive two synthetic snapshots, audit them, verify the report: high
    correlations overall, the deliberately 'reworked' champion is the top mover,
    and re-archiving identical content is skipped."""
    import tempfile

    ok = True
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        raw_a, raw_b = tmp / "raw_a", tmp / "raw_b"
        snaps, reports = tmp / "snapshots", tmp / "reports"

        _write_synthetic_snapshot(raw_a, lambda *a: 0.0,
                                  patch="16.98", fetched_at="2026-01-01T00:00:00+00:00")

        def shift(mode, ra, rb, row_champ, col_champ):
            if row_champ == "Zed":       # the rework: Zed's rows move hard
                return 3.0
            return 0.15                  # everyone else: mild drift
        _write_synthetic_snapshot(raw_b, shift,
                                  patch="16.99", fetched_at="2026-01-15T00:00:00+00:00")

        da = archive_snapshot(raw_a, snaps, quiet=True)
        db = archive_snapshot(raw_b, snaps, quiet=True)
        if da is None or db is None:
            print("  FAIL: archive returned None"); return False
        if da.name != "16.98_2026-01-01" or db.name != "16.99_2026-01-15":
            print(f"  FAIL: unexpected snapshot names {da.name}, {db.name}"); ok = False
        if not json.loads((da / MANIFEST_NAME).read_text())["files"]:
            print("  FAIL: manifest has no file hashes"); ok = False
        if archive_snapshot(raw_a, snaps, quiet=True) != da:
            print("  FAIL: identical re-archive not skipped"); ok = False
        if len(list_snapshots(snaps)) != 2:
            print("  FAIL: expected exactly 2 snapshots"); ok = False

        report, report_path, cells_path = audit_snapshots(
            da, db, reports_dir=reports)
        if report["n_blocks"] != 8:  # 2 modes x 2x2 role pairs
            print(f"  FAIL: expected 8 blocks, got {report['n_blocks']}"); ok = False
        if report["n_shared_cells"] != 72:  # 8 blocks x 9 cells
            print(f"  FAIL: expected 72 shared cells, got {report['n_shared_cells']}"); ok = False
        movers = report["top_movers"]
        if not movers or movers[0]["champion"] != "Zed":
            print(f"  FAIL: expected Zed as top mover, got "
                  f"{movers[0]['champion'] if movers else 'nothing'}"); ok = False
        if not report_path.exists() or not cells_path.exists():
            print("  FAIL: report/cells file missing"); ok = False
        with gzip.open(cells_path, "rt", encoding="utf-8") as f:
            cells = json.load(f)["cells"]
        if len(cells["z_a"]) != report["n_shared_cells"]:
            print("  FAIL: cells file row count mismatch"); ok = False
        # graceful degradation with a single snapshot
        solo = tmp / "solo"
        solo.mkdir()
        if run_audit_cli(None, None, warn_corr=0.9, top=5,
                         snapshots_dir=solo) != 0:
            print("  FAIL: <2 snapshots should exit 0 (graceful)"); ok = False

        if ok:
            print_summary(report)
    print("  OK: snapshot archive+audit self-test passed" if ok
          else "  self-test FAILED")
    return ok


def main(argv=None):
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    p = argparse.ArgumentParser(prog="lol-draft-snapshot")
    p.add_argument("--selftest", action="store_true",
                   help="run the offline synthetic-fixture self-test and exit")
    args = p.parse_args(argv)
    if args.selftest:
        raise SystemExit(0 if _selftest() else 1)
    p.print_help()


if __name__ == "__main__":
    main()
