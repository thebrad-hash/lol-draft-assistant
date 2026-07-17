# BRADDRAFT desktop app — live champ-select sync for friends (no host needed)

The cloud (Vercel) deployment can't read a local League client — live sync only
works when the app runs on the **same PC as League**. This packages the whole app
(engine, web UI, data, and live sync) into one Windows executable that anyone can
run with **no Python install and no dependency on you**.

## Premade multiplayer (friends without you)

Lobbies are shared through the **public website** (Redis). Desktop **proxies**
lobby create / join / chat / live-broadcast to
`https://lol-draft-assistant-rho.vercel.app` by default
(`BRADDRAFT_LOBBY_ORIGIN`). So:

1. Anyone: desktop or website → **+ Premade** → **Share lobby** (link is always the public site).
2. Friends open that link **in the browser** — set name, role, pool; they follow live draft.
3. **Whoever is in champ select** runs **BRADDRAFT.exe**, joins the same lobby
   (paste link, or `BRADDRAFT.exe "https://…/?lobby=ID"`), leaves **Go Live** on.
4. Their client is read locally; the draft is pushed to the shared lobby. Everyone
   else auto-fills. You do not need to be online.

Optional env: `BRADDRAFT_LOBBY_ORIGIN=local` for offline-only lobbies (no friends on the site).
`BRADDRAFT_PUBLIC_ORIGIN` overrides the share-link origin.

This is the complement to the hosted site:

| Use | What | Live sync |
|---|---|---|
| Access anywhere / share a link / follow live draft | the Vercel URL | follow only |
| Auto-read **your** champ select + broadcast to premade | **BRADDRAFT.exe** | **yes** |

## Build it (Windows)
```
cd web  &  npm run build  &  cd ..      REM build the UI into web/dist
build_desktop.bat                       REM -> dist\BRADDRAFT.exe (~40 MB)
```
The build bundles the whole `data/models` folder, not just `winprob.json`: the
`*_bootstrap.json` ensembles drive the win-probability **error bars** and the
"too close to call" tie flags (the heart of the UI), and the per-patch models
feed the dataset toggle. The app icon comes from `desktop/braddraft.ico`.

## Give it to friends
Share `dist\BRADDRAFT.exe` via a **GitHub Release** (repo → Releases →
Draft a new release → attach the `.exe`) or any file host. Each friend:
1. Downloads the `.exe`.
2. Double-clicks it. Windows SmartScreen may warn (unsigned app) → **More info →
   Run anyway**. (Unsigned ≠ unsafe; code-signing costs money.)
3. Their browser opens to **http://localhost:8000** — the full app.
4. With League running, **Go Live** syncs their champ select. In a premade, the
   status shows **Live · broadcasting** and friends on the share link update live.

Only the **in-game** player needs the `.exe`. Everyone else can stay on the website.

**Mac/Linux friends:** PyInstaller builds are per-OS, so a Windows `.exe` is
Windows-only. They run from source instead: `pip install -r requirements.txt`
then `python -m lol_draft.server`, and open `localhost:8000`.

## Notes
- The serve path is numpy-free, so the bundle stays small/fast.
- Rebuild after any code/UI/model change (rebuild `web/dist` first).
- The `.exe`, `build/`, and `*.spec` are gitignored — distribute the binary via a
  Release, not the repo. The tracked build inputs are `launcher.py`,
  `build_desktop.bat`, and `desktop/braddraft.ico`.
