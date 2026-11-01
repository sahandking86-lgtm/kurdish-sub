@echo off
REM ============================================================
REM  Kurdish Sub - one click start for Windows
REM  First run: installs everything (a few minutes, one time)
REM  Later runs: starts instantly
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Kurdish Sub - Sorani subtitles

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo  Python was not found.
  echo  Install Python 3.10 or newer from https://www.python.org/downloads/windows/
  echo  During setup tick "Add python.exe to PATH", then run this file again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo  Creating the local environment - one time only ...
  %PY% -m venv .venv
  if errorlevel 1 goto :error
)

set "VPY=.venv\Scripts\python.exe"

if not exist ".venv\.installed" (
  echo  Installing the engines ^(this can take a few minutes^) ...
  "%VPY%" -m pip install --upgrade pip --quiet
  "%VPY%" -m pip install -r requirements.txt
  if errorlevel 1 goto :error
  echo ok > ".venv\.installed"
)

echo.
echo  Starting Kurdish Sub - your browser will open in a moment.
echo  Keep this window open while you use the app. Press Ctrl+C to stop.
echo.
"%VPY%" -m app %*
pause
exit /b 0

:error
echo.
echo  Something went wrong while installing. Check your internet connection and try again.
echo.
pause
exit /b 1
