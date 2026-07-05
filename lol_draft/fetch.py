"""Download + cache machineloling's static data files.

The files are static assets on GitHub Pages (Fastly CDN, CORS-open, no robots
restriction). We fetch once, cache to data/raw/, and only re-fetch on demand.
A sidecar `<file>.meta.json` records the fetch timestamp, source URL and size.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config


def _meta_path(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".meta.json")


def riot_live_patch(timeout: float = 10.0) -> str | None:
    """Current live LoL patch as major.minor (e.g. "16.13"), or None on failure.

    The machineloling files carry no game-patch field anywhere, so the snapshot
    archive stamps each fetch with Riot's own current-version list instead. A
    failure here must never block a fetch — callers treat None as "unknown"."""
    try:
        req = urllib.request.Request(
            config.RIOT_VERSIONS_URL, headers={"User-Agent": config.USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            versions = json.loads(resp.read())
        latest = versions[0]  # e.g. "16.13.1"
        parts = str(latest).split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else None
    except Exception:
        return None


_PATCH_UNRESOLVED = object()  # sentinel: fetch_file should look it up itself


def fetch_file(name: str, *, force: bool = False, raw_dir: Path | None = None,
               riot_patch=_PATCH_UNRESOLVED) -> Path:
    raw_dir = raw_dir or config.RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / name
    if dest.exists() and not force:
        return dest

    url = f"{config.DATA_BASE_URL}/{name}"
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    dest.write_bytes(data)
    if riot_patch is _PATCH_UNRESOLVED:
        riot_patch = riot_live_patch()
    _meta_path(dest).write_text(
        json.dumps(
            {
                "url": url,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "bytes": len(data),
                "riot_patch": riot_patch,  # live patch at fetch time; None if lookup failed
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


def fetch_all(*, force: bool = False, raw_dir: Path | None = None) -> dict[str, Path]:
    """Ensure all data files are present locally; return {name: path}."""
    raw_dir = raw_dir or config.RAW_DIR
    # Resolve the live patch ONCE per fetch batch, and only if something will
    # actually be downloaded (a warm cache with force=False never hits Riot).
    downloading = force or any(not (raw_dir / n).exists() for n in config.DATA_FILES)
    patch = riot_live_patch() if downloading else None
    return {
        name: fetch_file(name, force=force, raw_dir=raw_dir, riot_patch=patch)
        for name in config.DATA_FILES
    }


def fetch_meta(raw_dir: Path | None = None) -> dict[str, dict]:
    """Return the recorded fetch metadata per file (empty dict if missing)."""
    raw_dir = raw_dir or config.RAW_DIR
    out: dict[str, dict] = {}
    for name in config.DATA_FILES:
        mp = _meta_path(raw_dir / name)
        if mp.exists():
            out[name] = json.loads(mp.read_text(encoding="utf-8"))
    return out


if __name__ == "__main__":  # python -m lol_draft.fetch [--force]
    import sys

    force = "--force" in sys.argv
    paths = fetch_all(force=force)
    for name, p in paths.items():
        print(f"{name}: {p} ({p.stat().st_size} bytes)")
