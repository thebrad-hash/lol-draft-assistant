# Deploying to Vercel

The app ships as a **static React UI + a Python serverless API** on Vercel:

- `web/` → built by Vite to `web/dist` and served as static files.
- `api/index.py` → the FastAPI engine (`lol_draft.server`) as a serverless function;
  `/api/*` is routed to it by `vercel.json`.
- `data/draft.db` and `data/models/winprob.json` are **committed** and bundled with
  the function (`vercel.json` → `includeFiles`), so it serves with no build step.
  The serve path is numpy-free, so the function installs only `api/requirements.txt`
  (`fastapi`) — small bundle, fast cold start.

## Deploy

**Option A — GitHub (recommended):** in the Vercel dashboard, *Add New → Project*,
import `thebrad-hash/lol-draft-assistant`, pick the `draft-assistant-features`
branch (or merge to `main` first). Vercel reads `vercel.json`; just click **Deploy**.
Every push redeploys.

**Option B — CLI:** from the repo root, `npx vercel` (first run links/creates the
project), then `npx vercel --prod`. (Rebuild `web/dist` first so the function
serves the latest UI: `cd web && npm run build && cd ..`.)

**Environment variables:** add **Upstash Redis** so the premade lobby + live
broadcast are shared across stateless invocations (see "Premade live draft"
below). Optional: `WINPROB_TIE_THRESHOLD` (default 0.85).

## What works hosted vs. not

- ✅ Recommendations, full-draft evaluation, "who picks next" — all calibrated win%.
- ✅ **Premade lobby + chat + live draft** — once Upstash is configured.
- ⚙️ **Live champ-select sync** — a cloud server can't read anyone's *local* League
  client, so every web client is in **follow mode**. The live draft is fed by a
  local **broadcaster** the in-game player runs (see below). The host-reads-own-client
  path still works in the local/tunnel setup (`python -m lol_draft.server` +
  `cloudflared tunnel --url http://localhost:8000`).

## Premade live draft (Upstash + desktop broadcast)

The "no designated host" setup: nobody has to be hosting or even playing for the
premade to use it — **whoever is in the game** broadcasts the draft to everyone.

**Preferred path (no extra tool):** the in-game player runs **BRADDRAFT.exe**
(desktop). Local lobbies proxy to this public site by default, so Go Live both
reads LCU and publishes into the same Redis lobby friends follow in the browser.
See [`DESKTOP.md`](DESKTOP.md). The standalone `braddraft_broadcast.py` / `.exe`
remains as a lightweight alternative if someone only wants to push LCU without
the full app.

### 1. Add Upstash Redis (required for lobby/live; ≈2 min)

Vercel functions are stateless and don't share memory, so the lobby + broadcast
must live in Redis. `lobby_store.py` already speaks it — just add credentials:

- **Easiest:** Vercel dashboard → project → **Storage → Marketplace → Upstash → Redis**
  (free tier). It auto-sets `KV_REST_API_URL` + `KV_REST_API_TOKEN`.
- **Manual:** create a DB at <https://console.upstash.com>, copy the **REST** URL +
  token, and add `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` under
  project **Settings → Environment Variables**.

Redeploy (`npx vercel --prod`) after adding them so the function picks them up.

### 2. Game-day flow

1. One person: **+ Premade → Share lobby**, send the link (public site URL).
2. Everyone opens it (website is enough), sets username + role(s) + pool. Live
   auto-enables; until someone broadcasts it shows *"waiting for live draft."*
3. **Whoever is in champ select** runs desktop (recommended):
   - Double-click **BRADDRAFT.exe**, **Join** with the share link (or
     `BRADDRAFT.exe "https://…/?lobby=ID"`), leave **Go Live** on.
   - Status: **Live · broadcasting**. Friends on the site see the board fill.
   - Lightweight alternative (LCU push only): `braddraft-broadcast.exe` /
     `braddraft_broadcast.py` / `broadcast.bat` with the same share URL.
4. Everyone else just watches — the draft auto-fills, each from **their own** role.
   You do not need to be in the lobby.

Make it a double-click `.exe` for teammates without Python:

```bash
pip install pyinstaller
pyinstaller --onefile --name braddraft-broadcast braddraft_broadcast.py
# dist/braddraft-broadcast.exe "https://YOUR-APP.vercel.app/?lobby=ABC123" --name Brad
```

## Updating the deployed model / data

Retrain or rebuild locally, then commit the artifacts and push:

```bash
python -m lol_draft.cli build          # rebuild data/draft.db (committed)
python -m lol_draft.train --patch all  # rewrite data/models/winprob.json (committed)
git add data/draft.db data/models/winprob.json && git commit && git push
```

## Troubleshooting

- **`numpy` / module-not-found in the function** — the serve path must stay numpy-free
  (`store.py` imports `decode`/`fetch` lazily). Don't add numpy-importing code to the
  import path of `lol_draft.server`.
- **Function exceeds size limit** — ensure Vercel used `api/requirements.txt` (just
  `fastapi`), not the heavier repo-root `requirements.txt`.
- **Store/model not found at runtime** — confirm `vercel.json` `includeFiles: "data/**"`
  and that `data/draft.db` + `data/models/winprob.json` are committed (not gitignored).
- **Lobby/broadcast not shared between people** — Upstash env vars missing, or you didn't
  redeploy after adding them. Confirm `KV_REST_API_*` / `UPSTASH_REDIS_REST_*` exist in
  the Vercel project, then `npx vercel --prod`.
- **Friends stuck on "waiting for live draft"** — nobody is broadcasting, or the
  broadcaster can't read the client. Run it on the in-game machine with League open; it
  should print `[ok] broadcasting`. `HTTP 404` from it means a wrong/expired lobby id.
