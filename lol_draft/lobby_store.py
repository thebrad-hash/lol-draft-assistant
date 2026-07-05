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
import secrets
import threading
import time
import urllib.request

LOBBY_TTL = 6 * 3600  # seconds; refreshed on each write
_KEY = "lobby:{}"
_CHAT_KEY = "lobby:{}:chat"     # Redis list of JSON messages, capped to MAX_MESSAGES
MAX_MESSAGES = 60
MAX_TEXT = 300
# The host republishes its live draft every ~1.5s while in champ select; if we
# haven't heard one this recently the host has stopped (left select / closed the
# tab), so the broadcast goes stale and friends stop following it. Comparing the
# server's own clock to its own stamp avoids any host/friend clock-skew issues.
LIVE_STALE = 12


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
_chat: dict[str, list[dict]] = {}   # lobby id -> recent chat messages
_live_drafts: dict[str, dict] = {}  # lobby id -> {"draft": {...}, "ts": int}
_mem_lock = threading.Lock()


def _live_for(lid: str) -> tuple[dict | None, int | None, dict | None]:
    """The lobby's broadcast live draft, its server stamp, and the source member
    ({memberId, name}) — or (None, None, None) when absent or stale (the source
    stopped publishing; see LIVE_STALE)."""
    now = int(time.time())
    if redis_enabled():
        raw = _cmd("HGET", _KEY.format(lid), "__live")
        if not raw:
            return None, None, None
        try:
            o = json.loads(raw)
        except (ValueError, TypeError):
            return None, None, None
    else:
        with _mem_lock:
            o = _live_drafts.get(lid)
        if not o:
            return None, None, None
    ts = o.get("ts")
    if not isinstance(ts, int) or now - ts > LIVE_STALE:
        return None, None, None
    return o.get("draft"), ts, o.get("source")


def _messages(lid: str) -> list[dict]:
    """Recent chat messages for a lobby, oldest-first."""
    if redis_enabled():
        raw = _cmd("LRANGE", _CHAT_KEY.format(lid), 0, -1) or []
        out = []
        for x in raw:
            try:
                out.append(json.loads(x))
            except (ValueError, TypeError):
                pass
        return out
    with _mem_lock:
        return list(_chat.get(lid, []))


def _view(lid: str, members: dict[str, dict]) -> dict:
    draft, ts, source = _live_for(lid)
    return {
        "id": lid,
        "members": [{"memberId": mid, **m} for mid, m in members.items()],
        "messages": _messages(lid),
        "liveDraft": draft,
        "liveAt": ts,
        "liveSource": source,  # {memberId, name} of whoever is broadcasting, or null
    }


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
    # Copy and release before _view(): _view -> _messages re-acquires the
    # non-reentrant _mem_lock, so calling it under the lock self-deadlocks.
    with _mem_lock:
        members = _mem.get(lid)
        members = dict(members) if members is not None else None
    if members is None:
        return None
    return _view(lid, members)


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


def set_live_draft(lid: str, draft: dict | None, source: dict | None = None) -> dict | None:
    """Store (or clear, when draft is None) the lobby's broadcast live draft with a
    fresh server stamp and the source member ({memberId, name}). ANY member may be
    the source — whoever is actually in champ select pushes; last write wins.
    Returns the updated view, or None if the lobby is gone."""
    if redis_enabled():
        key = _KEY.format(lid)
        if not _cmd("EXISTS", key):
            return None
        if draft is None:
            _cmd("HDEL", key, "__live")
        else:
            _cmd("HSET", key, "__live",
                 json.dumps({"draft": draft, "ts": int(time.time()), "source": source}))
        _cmd("EXPIRE", key, LOBBY_TTL)
    else:
        with _mem_lock:
            if lid not in _mem:
                return None
            if draft is None:
                _live_drafts.pop(lid, None)
            else:
                _live_drafts[lid] = {"draft": draft, "ts": int(time.time()), "source": source}
    return get(lid)


def post_message(lid: str, member_id: str, name: str, text: str) -> dict | None:
    """Append a chat message to the lobby, capped to the last MAX_MESSAGES.
    Returns the updated view (members + messages), or None if the lobby is gone."""
    text = (text or "").strip()[:MAX_TEXT]
    if not text:
        return get(lid)
    msg = {
        "id": secrets.token_hex(4),
        "memberId": member_id,
        "name": (name or "Player")[:24],
        "text": text,
        "ts": int(time.time()),
    }
    if redis_enabled():
        key = _KEY.format(lid)
        if not _cmd("EXISTS", key):
            return None
        ckey = _CHAT_KEY.format(lid)
        _cmd("RPUSH", ckey, json.dumps(msg))
        _cmd("LTRIM", ckey, -MAX_MESSAGES, -1)
        _cmd("EXPIRE", ckey, LOBBY_TTL)
        _cmd("EXPIRE", key, LOBBY_TTL)
    else:
        with _mem_lock:
            if lid not in _mem:
                return None
            lst = _chat.setdefault(lid, [])
            lst.append(msg)
            del lst[:-MAX_MESSAGES]  # keep only the last MAX_MESSAGES
    return get(lid)
