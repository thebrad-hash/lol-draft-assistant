@echo off
REM Launcher for the LoL Draft Assistant.
REM Uses the Windows 'py' launcher: real install, not the Microsoft Store stub.
pushd "%~dp0"
py -3 -m lol_draft.cli %*
popd
