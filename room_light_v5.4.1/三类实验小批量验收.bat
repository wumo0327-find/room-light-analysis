@echo off
setlocal
cd /d "%~dp0room_light"
set "ROOMLIGHT_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%ROOMLIGHT_PY%" set "ROOMLIGHT_PY=%~dp0..\room_light_v5.2.0\.venv\Scripts\python.exe"
if not exist "%ROOMLIGHT_PY%" (
  echo Please run the V5 launcher first to prepare Python.
  pause
  exit /b 1
)
if "%~1"=="" (
  echo Drag the original experiment .rlproj onto this file.
  echo Fill the actual EPW in room_light\unified config JSON first.
  pause
  exit /b 1
)
"%ROOMLIGHT_PY%" run_common_experiments.py --project "%~f1"
pause
