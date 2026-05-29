@echo off
REM Launcher for the LoL Draft Assistant.
REM Uses the real Python install, not the Microsoft Store stub.
pushd "%~dp0"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m lol_draft.cli %*
popd
