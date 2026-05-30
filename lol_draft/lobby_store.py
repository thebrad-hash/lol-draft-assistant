"""Premade-lobby storage that survives serverless.

In-memory dict state doesn't persist across Vercel's stateless function
invocations, so a shared lobby would silently lose members. When Upstash Redis
REST credentials are present (env UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN)
lobbies live in Redis; otherwise we fall back to an in-process dict so local dev
needs zero setup. The HTTP/REST client is stdlib-only (urllib), keeping the
serverless function lean (no numpy/redis deps on the serve path).

Storage model (Redis): one hash per lobby at key `lobby:{id}`:
  - field `__exists` = "1"           (so an empty lobby is still a real lobby)
  - field `m:{memberId}` = JSON      ({name, role, pool})
HSET is atomic per field, so concurrent member updates don't clobber each other.
A TTL (LOBBY_TTL) is refreshed on every write; idle lobbies expire on their own.

Every function returns the web "lobby view" — {"id", "members": [{memberId, ...}]}
— or None when the lobby doesn't exist (the caller maps that to 404).
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request

LOBBY_TTL = 6 * 3600  # seconds; refreshed on each write
_KEY = "lobby:{}"


# --- backend selection ---
def _url() -> str | None:
    # UPSTASH_* (manual Upstash DB) or KV_REST_API_* (Vercel Marketplace integration)
    return os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")


def _token() -> str | None:
    return os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")


def redis_enabled() -> bool:
    return bool(_url() and _token())


def _cmd(*args):
    """Run one Redis command via the Upstash REST API; return its `result`."""
    body = json.dumps([str(a) for a in args]).encode()
    req = urllib.request.Request(
        _url().rstrip("/"),
        data=body,
        headers={"Authorization": f"Bearer {_token()}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        payload = json.loads(resp.read())
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"Upstash error: {payload['error']}")
    return payload.get("result") if isinstance(payload, dict) else payload


# --- in-memory fallback (local dev, no Redis creds) ---
_mem: dict[str, dict[str, dict]] = {}
_mem_lock = threading.Lock()


def _view(lid: str, members: dict[str, dict]) -> dict:
    return {"id": lid, "members": [{"memberId": mid, **m} for mid, m in members.items()]}


def create(lid: str) -> dict:
    if redis_enabled():
        key = _KEY.format(lid)
        _cmd("HSET", key, "__exists", "1")
        _cmd("EXPIRE", key, LOBBY_TTL)
    else:
        with _mem_lock:
            _mem[lid] = {}
    return {"id": lid, "members": []}


def get(lid: str) -> dict | None:
    if redis_enabled():
        flat = _cmd("HGETALL", _KEY.format(lid)) or []
        if not flat:
            return None
        h = {flat[i]: flat[i + 1] for i in range(0, len(flat) - 1, 2)}
        members = {k[2:]: json.loads(v) for k, v in h.items() if k.startswith("m:")}
        return _view(lid, members)
    with _mem_lock:
        members = _mem.get(lid)
        return _view(lid, members) if members is not None else None


def upsert_member(lid: str, member_id: str, data: dict) -> dict | None:
    if redis_enabled():
        key = _KEY.format(lid)
        if not _cmd("EXISTS", key):
            return None
        _cmd("HSET", key, f"m:{member_id}", json.dumps(data))
        _cmd("EXPIRE", key, LOBBY_TTL)
    else:
        with _mem_lock:
            if lid not in _mem:
                return None
            _mem[lid][member_id] = data
    return get(lid)


def remove_member(lid: str, member_id: str) -> dict | None:
    if redis_enabled():
        key = _KEY.format(lid)
        if not _cmd("EXISTS", key):
            return None
        _cmd("HDEL", key, f"m:{member_id}")
    else:
        with _mem_lock:
            if lid not in _mem:
                return None
            _mem[lid].pop(member_id, None)
    return get(lid)
