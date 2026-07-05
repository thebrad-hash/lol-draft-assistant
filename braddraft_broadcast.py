#!/usr/bin/env python3
"""BradDraft live broadcaster — push YOUR champ select to a shared premade lobby.

The live draft can only be read on the machine League runs on (the LCU API is
local-only). On a cloud deploy (Vercel) no web client can read a client, so
whoever is actually in the game runs THIS to feed the draft to everyone else —
no designated "host" needed, and you don't have to be the person hosting.

Usage (one file, just needs Python 3.9+ — no pip install):
    python braddraft_broadcast.py "https://your-app.vercel.app/?lobby=ABC123" --name Brad
    python braddraft_broadcast.py https://your-app.vercel.app --lobby ABC123
    python braddraft_broadcast.py <url> --demo      # test without being in a game
    python braddraft_broadcast.py <url> --once       # push once and exit

It polls your champ select ~every 1.5s and PUTs the shared draft (bans + both
teams, no per-player role) to <base>/api/lobby/<id>/live. Teammates open the same
link and auto-follow it, each with their own role. Ctrl-C to stop (clears the
broadcast). Can also be frozen to a double-click .exe (PyInstaller) for friends.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# LCU reader. Prefer the repo's canonical implementation (run from the repo or a
# frozen build); fall back to a compact inlined copy so this stays a single file.
# The fallback mirrors lol_draft/lcu.py — keep them in sync if that changes.
# ---------------------------------------------------------------------------
try:
    from lol_draft import lcu as _lcu  # type: ignore

    def read_live_draft() -> dict:
        return _lcu.live_draft()

    def local_name() -> str | None:
        try:
            c = _lcu.find_credentials()
            if not c:
                return None
            port, token = c
            d = _lcu._lcu_get(port, token, "/lol-chat/v1/me")
            return d.get("gameName") or d.get("name")
        except Exception:
            return None

except Exception:  # standalone single-file mode
    import base64
    import re
    import ssl
    from pathlib import Path

    POSITION_TO_ROLE = {"top": "TOP", "jungle": "JUNGLE", "middle": "MID",
                        "bottom": "BOT", "utility": "SUPPORT"}
    ROLE_ORDER = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
    _LOCKFILES = [
        Path(r"C:\Riot Games\League of Legends\lockfile"),
        Path(r"D:\Riot Games\League of Legends\lockfile"),
        Path(r"C:\Program Files\Riot Games\League of Legends\lockfile"),
    ]
    _ctx = ssl.create_default_context()
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE
    _champ_map: dict | None = None

    def _creds():
        for p in _LOCKFILES:
            try:
                if p.exists():
                    parts = p.read_text(encoding="utf-8").strip().split(":")
                    if len(parts) >= 4:
                        return int(parts[2]), parts[3]
            except Exception:
                pass
        if not sys.platform.startswith("win"):
            return None
        try:
            out = __import__("subprocess").run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\""
                 " | Select-Object -ExpandProperty CommandLine"],
                capture_output=True, text=True, timeout=8).stdout or ""
        except Exception:
            return None
        port = re.search(r"--app-port=(\d+)", out)
        token = re.search(r"--remoting-auth-token=([\w-]+)", out)
        return (int(port.group(1)), token.group(1)) if port and token else None

    def _get(port, token, path):
        auth = base64.b64encode(f"riot:{token}".encode()).decode()
        req = urllib.request.Request(
            f"https://127.0.0.1:{port}{path}",
            headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
        with urllib.request.urlopen(req, context=_ctx, timeout=5) as r:
            return json.loads(r.read())

    def _champion_map(port, token):
        global _champ_map
        if _champ_map is None:
            data = _get(port, token, "/lol-game-data/assets/v1/champion-summary.json")
            _champ_map = {int(c.get("id", 0)): (c.get("alias") or c.get("name"))
                          for c in data if int(c.get("id", 0)) > 0 and (c.get("alias") or c.get("name"))}
        return _champ_map

    def _infer(champ, taken):
        for cand in ROLE_ORDER:
            if cand not in taken:
                return cand
        return None

    def _session_to_draft(session, cmap):
        def key(cid):
            cid = int(cid or 0)
            return cmap.get(cid) if cid > 0 else None

        bans = []
        for group in session.get("actions", []) or []:
            for a in group:
                if a.get("type") == "ban" and a.get("completed") and int(a.get("championId") or 0) > 0:
                    k = key(a["championId"])
                    if k and k not in bans:
                        bans.append(k)

        def map_team(members):
            team, positioned, floating = {}, [], []
            for m in members or []:
                champ = key(m.get("championId"))
                if not champ:
                    continue
                role = POSITION_TO_ROLE.get((m.get("assignedPosition") or "").lower())
                (positioned if role else floating).append((champ, role))
            for champ, role in positioned:
                team.setdefault(role, champ)
            for champ, _ in floating:
                r = _infer(champ, team)
                if r:
                    team[r] = champ
            return team
        return {"bans": bans, "myTeam": map_team(session.get("myTeam")),
                "enemyTeam": map_team(session.get("theirTeam")), "pickingForRole": None}

    def read_live_draft() -> dict:
        creds = _creds()
        if not creds:
            return {"connected": False, "inChampSelect": False, "reason": "League client not detected"}
        port, token = creds
        try:
            cmap = _champion_map(port, token)
            session = _get(port, token, "/lol-champ-select/v1/session")
        except urllib.error.HTTPError as e:
            return {"connected": True, "inChampSelect": False,
                    "reason": "not in champ select" if e.code == 404 else f"HTTP {e.code}"}
        except Exception as e:
            return {"connected": False, "inChampSelect": False, "reason": str(e)}
        return {"connected": True, "inChampSelect": True, "draft": _session_to_draft(session, cmap)}

    def local_name():
        try:
            creds = _creds()
            if not creds:
                return None
            port, token = creds
            d = _get(port, token, "/lol-chat/v1/me")
            return d.get("gameName") or d.get("name")
        except Exception:
            return None


_DEMO = {
    "connected": True, "inChampSelect": True, "demo": True,
    "draft": {"bans": ["Garen", "Yasuo", "Yone", "Kayle", "Akali"],
              "myTeam": {"TOP": "Aatrox", "JUNGLE": "LeeSin", "MID": "Ahri",
                         "BOT": "Jinx", "SUPPORT": "Thresh"},
              "enemyTeam": {"TOP": "Darius", "JUNGLE": "Sejuani", "MID": "Zed",
                            "BOT": "Caitlyn", "SUPPORT": "Lulu"},
              "pickingForRole": "MID"},
}


def _parse_target(raw: str, lobby_arg: str | None) -> tuple[str, str]:
    """Return (base_url, lobby_id) from a share URL (…/?lobby=ID) or base + --lobby."""
    u = urllib.parse.urlsplit(raw if "://" in raw else "https://" + raw)
    lobby = lobby_arg or urllib.parse.parse_qs(u.query).get("lobby", [None])[0]
    if not lobby:
        sys.exit("error: no lobby id — pass a share URL with ?lobby=… or use --lobby ID")
    base = f"{u.scheme}://{u.netloc}"
    if not u.netloc:
        sys.exit(f"error: could not parse a base URL from {raw!r}")
    return base, lobby


def _publish(base: str, lobby: str, body: dict) -> tuple[bool, str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}/api/lobby/{urllib.parse.quote(lobby)}/live",
        data=data, method="PUT", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200, str(r.status)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}" + (" (lobby not found)" if e.code == 404 else "")
    except Exception as e:
        return False, str(e)


def main() -> None:
    ap = argparse.ArgumentParser(description="Broadcast your live champ select to a BradDraft lobby.")
    ap.add_argument("url", help="share URL (…/?lobby=ID) or the app base URL")
    ap.add_argument("--lobby", help="lobby id (if the URL has no ?lobby=)")
    ap.add_argument("--name", default=None, help="your name (default: auto-read from your League client)")
    ap.add_argument("--interval", type=float, default=1.5, help="poll seconds (default 1.5)")
    ap.add_argument("--demo", action="store_true", help="send a synthetic draft (test without a game)")
    ap.add_argument("--once", action="store_true", help="push once and exit")
    args = ap.parse_args()

    base, lobby = _parse_target(args.url, args.lobby)
    # default the display name to your League name (so the .bat needs no config)
    name = args.name or local_name() or "A teammate"
    # stable-ish id for this broadcaster run, so the source is consistent
    member_id = "bcast-" + str(abs(hash((name, base, lobby))) % 10**8)
    print(f"BradDraft broadcaster -> {base}  lobby={lobby}  as {name!r}")
    print("Reading your local League client; teammates following the link will auto-fill.")
    print("Ctrl-C to stop.\n")

    published = False
    while True:
        st = _DEMO if args.demo else read_live_draft()
        if st.get("inChampSelect") and st.get("draft"):
            d = st["draft"]
            ok, info = _publish(base, lobby, {
                "draft": {"bans": d.get("bans", []), "myTeam": d.get("myTeam", {}),
                          "enemyTeam": d.get("enemyTeam", {}), "pickingForRole": None},
                "memberId": member_id, "name": name,
            })
            published = published or ok
            n_my, n_en = len(d.get("myTeam", {})), len(d.get("enemyTeam", {}))
            print(f"\r{'[ok]' if ok else '[!!]'} broadcasting - {n_my} ally / {n_en} enemy picks, "
                  f"{len(d.get('bans', []))} bans  [{info}]   ", end="", flush=True)
        else:
            if published:
                _publish(base, lobby, {"draft": None, "memberId": member_id, "name": name})
                published = False
            reason = st.get("reason", "not in champ select")
            print(f"\r... waiting: {reason}                              ", end="", flush=True)
        if args.once:
            print()
            return
        try:
            time.sleep(max(0.5, args.interval))
        except KeyboardInterrupt:
            break

    if published:
        _publish(base, lobby, {"draft": None, "memberId": member_id, "name": name})
    print("\nstopped — broadcast cleared.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
