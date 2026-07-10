@echo off
REM GarminGPT - one-click setup + launch for Windows.
REM Double-click this file. First run installs everything (a few minutes).
REM Keep the window open while using the app. Close it to stop.
setlocal enableextensions
cd /d "%~dp0"
title GarminGPT

echo(
echo GarminGPT - your private health ML dashboard
echo First run sets everything up. Nothing leaves this computer.
echo(

set "MODEL=qwen2.5:3b"
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Programs\Ollama;%PATH%"

REM 1) Ollama (local AI engine)
echo == Local AI engine (Ollama) ==
where ollama >nul 2>nul
if errorlevel 1 (
  echo Installing Ollama via winget...
  winget install --silent --accept-source-agreements --accept-package-agreements Ollama.Ollama
  set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
)
where ollama >nul 2>nul
if errorlevel 1 (
  echo(
  echo Could not install Ollama automatically.
  echo Please install it from https://ollama.com/download  then run this file again.
  echo Or paste this folder's README into the Claude or ChatGPT app and ask for help.
  pause
  exit /b 1
)

REM start the Ollama server in the background
start "" /b ollama serve
timeout /t 4 /nobreak >nul

REM 2) uv (installs Python + dependencies)
echo == Python tooling ==
where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv...
  powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
where uv >nul 2>nul
if errorlevel 1 (
  echo Could not install uv. Paste this folder's README into Claude or ChatGPT and ask for help.
  pause
  exit /b 1
)

echo == App dependencies ==
uv sync
if errorlevel 1 ( echo Dependency install failed. & pause & exit /b 1 )

REM 3) the model
echo == AI model %MODEL% ==
echo Downloading the AI model - the one big download, a few minutes...
ollama pull %MODEL%
if errorlevel 1 ( echo Could not download the model. & pause & exit /b 1 )

REM 4) launch
echo == Starting your dashboard ==
set "GARMINGPT_MODEL=%MODEL%"
start "" http://127.0.0.1:8800
echo Opening http://127.0.0.1:8800 - sign in to Garmin there.
echo Keep this window open while using the app. Close it to stop.
uv run python -m garmingpt serve
