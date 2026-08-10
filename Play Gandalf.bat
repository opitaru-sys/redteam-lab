@echo off
title Gandalf attacker loop
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo Node.js was not found on PATH.
  echo Install Node 22 or newer from https://nodejs.org and run this again.
  echo.
  pause
  exit /b 1
)

node "%~dp0gandalf.mjs" menu

echo.
pause
