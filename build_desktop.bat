@echo off
REM Build the standalone desktop app: one-file Windows .exe that bundles the
REM engine + web UI + data + live champ-select sync. Friends run it with no
REM Python install and no dependency on anyone else. Output: dist\lol-draft-assistant.exe
REM
REM Build the web UI first:   cd web  ^&  npm run build   ^&  cd ..
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --onefile --name lol-draft-assistant ^
  --add-data "data/draft.db;data" ^
  --add-data "data/models/winprob.json;data/models" ^
  --add-data "web/dist;web/dist" ^
  --collect-all uvicorn ^
  --noconfirm launcher.py
echo.
echo Built: dist\lol-draft-assistant.exe  --  share this file with friends.
