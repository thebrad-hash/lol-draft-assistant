# Desktop app — live champ-select sync for friends (no host needed)

The cloud (Vercel) deployment can't read a local League client — live sync only
works when the app runs on the **same PC as League**. This packages the whole app
(engine, web UI, data, and live sync) into one Windows executable that anyone can
run with **no Python install and no dependency on you**.

This is the complement to the hosted site:

| Use | What | Live sync |
|---|---|---|
| Access anywhere / share a link / manual entry | the Vercel URL | no |
| Auto-read your (or a friend's) own champ select | **this desktop .exe** | **yes** |

## Build it (Windows)
```
cd web  &  npm run build  &  cd ..      REM build the UI into web/dist
build_desktop.bat                       REM -> dist\lol-draft-assistant.exe (~39 MB)
```

## Give it to friends
Share `dist\lol-draft-assistant.exe` via a **GitHub Release** (repo → Releases →
Draft a new release → attach the `.exe`) or any file host. Each friend:
1. Downloads the `.exe`.
2. Double-clicks it. Windows SmartScreen may warn (unsigned app) → **More info →
   Run anyway**. (Unsigned ≠ unsafe; code-signing costs money.)
3. Their browser opens to **http://localhost:8000** — the full app.
4. With League running, **Go Live** syncs their champ select. Entirely on their
   machine, independent of you.

**Mac/Linux friends:** PyInstaller builds are per-OS, so a Windows `.exe` is
Windows-only. They run from source instead: `pip install -r requirements.txt`
then `python -m lol_draft.server`, and open `localhost:8000`.

## Notes
- The serve path is numpy-free, so the bundle stays small/fast.
- Rebuild after any code/UI/model change (rebuild `web/dist` first).
- The `.exe` and `build/` are gitignored — distribute the binary via a Release,
  not the repo.
