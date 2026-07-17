"""Desktop launcher for the standalone (PyInstaller) build.

Bundled into a one-file Windows executable so friends get the full app **and**
live champ-select sync with no Python install and no dependency on anyone else:
double-click -> a local server starts and the browser opens to the app. Because
it runs on their own machine, it can read their local League client (read-only),
which the cloud-hosted Vercel copy can never do.

Premade multiplayer: by default the local process proxies lobby create/join/
chat/live-broadcast to the public BradDraft site (see lobby_store.lobby_remote_origin).
So whoever is in champ select runs this .exe + Go Live, and everyone else only
needs the website share link.

Optional CLI:
    BRADDRAFT.exe
    BRADDRAFT.exe "https://lol-draft-assistant-rho.vercel.app/?lobby=ABC123"
    BRADDRAFT.exe --lobby ABC123

Build (from the repo root, with the web UI already built into web/dist):
    pip install pyinstaller
    pyinstaller lol-draft-assistant.spec        # or the CLI in build_desktop.*
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser

import uvicorn

# Ensure remote lobbies hit the public app before importing the server (which
# reads env at request time — setdefault is still the right place for defaults).
os.environ.setdefault(
    "BRADDRAFT_LOBBY_ORIGIN",
    "https://lol-draft-assistant-rho.vercel.app",
)
os.environ.setdefault(
    "BRADDRAFT_PUBLIC_ORIGIN",
    "https://lol-draft-assistant-rho.vercel.app",
)

from lol_draft.server import app  # noqa: E402

HOST, PORT = "127.0.0.1", 8000
BASE = f"http://{HOST}:{PORT}"


def _lobby_id_from_argv(argv: list[str]) -> str | None:
    """Accept a full share URL, --lobby ID, or a bare lobby id token."""
    args = list(argv[1:])
    for i, a in enumerate(args):
        if a in ("--lobby", "-l") and i + 1 < len(args):
            return args[i + 1].strip() or None
        if a.startswith("--lobby="):
            return a.split("=", 1)[1].strip() or None
        if "lobby=" in a:
            try:
                # URL or query fragment
                if "://" in a or a.startswith("?"):
                    q = urllib.parse.urlparse(a if "://" in a else f"http://x{a}").query
                else:
                    q = a
                lid = urllib.parse.parse_qs(q).get("lobby", [None])[0]
                if lid:
                    return lid.strip()
            except Exception:
                pass
            m = re.search(r"[?&]lobby=([^&]+)", a)
            if m:
                return urllib.parse.unquote(m.group(1)).strip()
        # bare token (urlsafe ids are typically 8+ chars)
        if re.fullmatch(r"[A-Za-z0-9_-]{6,24}", a) and not a.startswith("-"):
            return a
    return None


def _open_browser(lobby_id: str | None) -> None:
    time.sleep(1.5)  # give uvicorn a moment to bind
    url = BASE if not lobby_id else f"{BASE}/?lobby={urllib.parse.quote(lobby_id)}"
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    lobby_id = _lobby_id_from_argv(sys.argv)
    print("=" * 60)
    print("  BRADDRAFT  ·  Best Rotations And Designations")
    print(f"  Open {BASE} in your browser (opening it now).")
    print("  Live champ-select sync works while League is running here.")
    if lobby_id:
        print(f"  Joining lobby {lobby_id!r} — Go Live broadcasts to your premade.")
    else:
        print("  Premade: create/share a lobby (link opens the public site for friends).")
        print("  In champ select: leave Go Live on — teammates on the site auto-fill.")
    print("  Close this window to stop.")
    print("=" * 60)
    threading.Thread(target=_open_browser, args=(lobby_id,), daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
