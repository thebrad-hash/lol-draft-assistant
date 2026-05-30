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
project), then `npx vercel --prod`.

No environment variables are required.

## What works hosted vs. not

- ✅ Recommendations, full-draft evaluation, "who picks next" — all calibrated win%.
- ❌ **"Go Live" champ-select sync** — reads your *local* League client, which a cloud
  server can't reach. It degrades gracefully ("no client"). For live sync, run locally
  (`python -m lol_draft.server` + `cd web && npm run dev`) or tunnel
  (`cloudflared tunnel --url http://localhost:8000`).
- ⚠️ **Premade lobby** — in-memory, so it won't persist across serverless invocations.

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
