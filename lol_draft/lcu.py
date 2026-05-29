"""Live champ-select connector via the LCU (League Client Update) API.

The League client runs a local HTTPS server on 127.0.0.1 with a random port and
an auth token, discoverable from the client process args (or the lockfile).
We READ ONLY: poll `/lol-champ-select/v1/session` and translate it into the same
UI-shaped draft the web app uses. We never automate in-client actions.

Champion ids come back numeric; we map them to string keys (e.g. 64 -> "LeeSin")
using the client's own `champion-summary.json` — so no external/Data Dragon call
is needed. Those keys match machineloling's roster ids.

Everything here degrades gracefully: if the client isn't running or you're not in
champ select, callers get a status flag rather than an exception.
"""
from __future__ import annotations

import base64
import json
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# LCU assignedPosition -> our UI role keys
POSITION_TO_ROLE = {
    "top": "TOP",
    "jungle": "JUNGLE",
    "middle": "MID",
    "bottom": "BOT",
    "utility": "SUPPORT",
}

_COMMON_LOCKFILES = [
    Path(r"C:\Riot Games\League of Legends\lockfile"),
    Path(r"D:\Riot Games\League of Legends\lockfile"),
    Path(r"C:\Program Files\Riot Games\League of Legends\lockfile"),
]

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

_creds: Optional[tuple[int, str]] = None
_champ_map: Optional[dict[int, str]] = None


# --- credential discovery ---
def _creds_from_process() -> Optional[tuple[int, str]]:
    """Read --app-port / --remoting-auth-token from the LeagueClientUx process."""
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\""
                " | Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True, text=True, timeout=8,
        ).stdout or ""
    except Exception:
        return None
    port = re.search(r"--app-port=(\d+)", out)
    token = re.search(r'--remoting-auth-token=([\w-]+)', out)
    if port and token:
        return int(port.group(1)), token.group(1)
    return None


def _creds_from_lockfile() -> Optional[tuple[int, str]]:
    for p in _COMMON_LOCKFILES:
        try:
            if p.exists():
                # format: name:pid:port:password:protocol
                parts = p.read_text(encoding="utf-8").strip().split(":")
                if len(parts) >= 4:
                    return int(parts[2]), parts[3]
        except Exception:
            continue
    return None


def find_credentials(force: bool = False) -> Optional[tuple[int, str]]:
    global _creds
    if _creds and not force:
        return _creds
    _creds = _creds_from_process() or _creds_from_lockfile()
    return _creds


# --- LCU HTTP ---
def _lcu_get(port: int, token: str, path: str):
    url = f"https://127.0.0.1:{port}{path}"
    auth = base64.b64encode(f"riot:{token}".encode()).decode()
    req = urllib.request.Request(
        url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, context=_ssl_ctx, timeout=5) as resp:
        return json.loads(resp.read())


def champion_map(port: int, token: str) -> dict[int, str]:
    global _champ_map
    if _champ_map is None:
        data = _lcu_get(port, token, "/lol-game-data/assets/v1/champion-summary.json")
        _champ_map = {}
        for c in data:
            cid = int(c.get("id", 0))
            alias = c.get("alias") or c.get("name")
            if cid > 0 and alias:
                _champ_map[cid] = alias
    return _champ_map


# --- mapping: LCU session -> UI draft ---
def session_to_draft(session: dict, cmap: dict[int, str]) -> dict:
    def key(cid) -> Optional[str]:
        cid = int(cid or 0)
        return cmap.get(cid) if cid > 0 else None

    # bans: prefer completed ban actions (most reliable across queues)
    bans: list[str] = []
    for group in session.get("actions", []) or []:
        for a in group:
            if a.get("type") == "ban" and a.get("completed") and int(a.get("championId") or 0) > 0:
                k = key(a["championId"])
                if k and k not in bans:
                    bans.append(k)
    # also merge any explicit bans object, if present
    b = session.get("bans") or {}
    for cid in (b.get("myTeamBans", []) or []) + (b.get("theirTeamBans", []) or []):
        k = key(cid)
        if k and k not in bans:
            bans.append(k)

    local_cell = session.get("localPlayerCellId")
    picking_for: Optional[str] = None
    my_team: dict[str, str] = {}
    enemy_team: dict[str, str] = {}

    for m in session.get("myTeam", []) or []:
        role = POSITION_TO_ROLE.get((m.get("assignedPosition") or "").lower())
        if m.get("cellId") == local_cell and role:
            picking_for = role
        k = key(m.get("championId"))
        if role and k:
            my_team[role] = k

    for m in session.get("theirTeam", []) or []:
        role = POSITION_TO_ROLE.get((m.get("assignedPosition") or "").lower())
        k = key(m.get("championId"))
        if role and k:  # usually empty during pick phase (enemy hidden)
            enemy_team[role] = k

    return {
        "bans": bans,
        "myTeam": my_team,
        "enemyTeam": enemy_team,
        "pickingForRole": picking_for,
    }


def live_draft() -> dict:
    """Return {connected, inChampSelect, draft|None, reason?}."""
    creds = find_credentials()
    if not creds:
        return {"connected": False, "inChampSelect": False, "draft": None,
                "reason": "League client not detected"}
    port, token = creds
    try:
        cmap = champion_map(port, token)
        session = _lcu_get(port, token, "/lol-champ-select/v1/session")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"connected": True, "inChampSelect": False, "draft": None}
        return {"connected": True, "inChampSelect": False, "draft": None,
                "reason": f"HTTP {e.code}"}
    except Exception as e:
        find_credentials(force=True)  # client may have restarted -> refresh next poll
        return {"connected": False, "inChampSelect": False, "draft": None, "reason": str(e)}

    return {"connected": True, "inChampSelect": True, "draft": session_to_draft(session, cmap)}


def demo_draft() -> dict:
    """A synthetic in-champ-select payload for previewing/testing without a game.
    (Shows enemy picks too, which a real ranked champ select would normally hide.)"""
    return {
        "connected": True,
        "inChampSelect": True,
        "demo": True,
        "draft": {
            "bans": ["Garen", "Yasuo", "Yone"],
            "myTeam": {"BOT": "Jinx", "SUPPORT": "Thresh", "JUNGLE": "Sejuani"},
            "enemyTeam": {"MID": "Ahri", "TOP": "Darius"},
            "pickingForRole": "MID",
        },
    }
