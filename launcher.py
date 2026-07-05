"""Desktop launcher for the standalone (PyInstaller) build.

Bundled into a one-file Windows executable so friends get the full app **and**
live champ-select sync with no Python install and no dependency on anyone else:
double-click -> a local server starts and the browser opens to the app. Because
it runs on their own machine, it can read their local League client (read-only),
which the cloud-hosted Vercel copy can never do.

Build (from the repo root, with the web UI already built into web/dist):
    pip install pyinstaller
    pyinstaller lol-draft-assistant.spec        # or the CLI in build_desktop.*
"""
from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn

from lol_draft.server import app

HOST, PORT = "127.0.0.1", 8000
URL = f"http://{HOST}:{PORT}"


def _open_browser() -> None:
    time.sleep(1.5)  # give uvicorn a moment to bind
    try:
        webbrowser.open(URL)
    except Exception:
        pass


def main() -> None:
    print("=" * 60)
    print("  BRADDRAFT  ·  Best Rotations And Designations")
    print(f"  Open {URL} in your browser (opening it now).")
    print("  Live champ-select sync works while League is running here.")
    print("  Close this window to stop.")
    print("=" * 60)
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
