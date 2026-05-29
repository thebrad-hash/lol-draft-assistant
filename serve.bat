@echo off
REM Starts the LoL Draft Assistant API (FastAPI) on http://127.0.0.1:8000
REM Then run the web UI separately:  cd web ^&^& npm run dev
pushd "%~dp0"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m lol_draft.server
popd
