"""Vercel serverless entrypoint — serves the FastAPI engine as a Python function.

vercel.json routes /api/* here and serves this ASGI `app`. We add the repo root
to sys.path so the `lol_draft` package resolves, and data/** is bundled with the
function (vercel.json `includeFiles`) so the SQLite store + win-probability model
load from disk.

Hosted limitations (by design — see the README/CLAUDE.md notes):
  - /api/live can't work: it reads the LOCAL League client, which a cloud server
    can't reach. It degrades gracefully ("no client").
  - the in-memory premade lobby won't persist across stateless invocations.
Everything else (recommend / evaluate / pick-order) runs from the committed
draft.db + model and needs no numpy at serve time.
"""
import os
import sys

# repo root = parent of this api/ directory; makes `import lol_draft...` work.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lol_draft.server import app  # noqa: E402  -- the ASGI app Vercel serves

# `app` is what @vercel/python serves.
