@echo off
REM Build the standalone BRADDRAFT desktop app: one-file Windows .exe bundling the
REM engine + web UI + data + live champ-select sync. Friends run it with no Python
REM install and no dependency on anyone else. Output: dist\BRADDRAFT.exe
REM
REM Build the web UI first:   cd web  ^&  npm run build   ^&  cd ..
REM
REM Bundles the ENTIRE data/models folder (not just winprob.json): the bootstrap
REM ensembles (*_bootstrap.json) drive the win-prob error bars + "too close to
REM call" flags, and the per-patch models feed the dataset toggle. Without them
REM the app silently falls back to bare point estimates (no confidence bars).
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --onefile --name BRADDRAFT ^
  --icon "desktop/braddraft.ico" ^
  --add-data "data/draft.db;data" ^
  --add-data "data/models;data/models" ^
  --add-data "web/dist;web/dist" ^
  --collect-all uvicorn ^
  --noconfirm launcher.py
echo.
echo Built: dist\BRADDRAFT.exe  --  share this file with friends.
