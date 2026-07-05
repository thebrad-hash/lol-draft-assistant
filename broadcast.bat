@echo off
title BradDraft Broadcaster
pushd "%~dp0"
echo(
echo   BradDraft live broadcaster
echo   ------------------------------------------------------------
echo   Feeds YOUR champ select into your premade's shared lobby so
echo   everyone following the link sees the draft (each with their
echo   own role). Keep this window open during champ select.
echo   Press Ctrl-C or close the window to stop.
echo(
set "LINK="
set /p "LINK=Paste your BradDraft share link, then press Enter: "
if "%LINK%"=="" (
  echo(
  echo No link entered - nothing to do.
  pause
  popd
  exit /b 1
)
echo(
py -3 braddraft_broadcast.py "%LINK%"
echo(
echo Broadcaster stopped.
pause
popd
