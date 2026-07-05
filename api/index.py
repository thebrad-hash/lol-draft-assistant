"""Vercel serverless entrypoint — serves the FastAPI engine as a Python function.

vercel.json routes /api/* here and serves this ASGI `app`. We add the repo root
to sys.path so the `lol_draft` package resolves, and data/** is bundled with the
function (vercel.json `includeFiles`) so the SQLite store + win-probability model
load from disk.

Hosted notes:
  - /api/live can't read a client here (the cloud server has no local League).
    Instead, whoever is in the game runs `braddraft_broadcast.py` locally, which
    pushes the draft to `PUT /api/lobby/{id}/live`; every web client follows it.
    `isLocal` is always false here, so the web UI is always in follow mode.
  - the premade lobby + live broadcast persist in Upstash Redis when its env vars
    are set (UPSTASH_REDIS_REST_* or Vercel's KV_REST_API_*). REQUIRED on Vercel:
    stateless invocations don't share the in-memory fallback, so without Redis a
    broadcaster's push and a friend's read can land on different instances. See
    DEPLOY.md.
Everything else (recommend / evaluate / pick-order) runs from the committed
draft.db + model and needs no numpy at serve time.
"""
import os
import sys

# repo root = parent of this api/ directory; makes `import lol_draft...` work.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lol_draft.server import app  # noqa: E402  -- the ASGI app Vercel serves

# `app` is what @vercel/python serves.
