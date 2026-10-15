@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [RoomLight V5] Creating the local Python environment...
  py -3 -m venv ".venv" || goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
  ".venv\Scripts\python.exe" -m pip install -r "room_light\requirements.txt" || goto :error
)
".venv\Scripts\python.exe" -c "import shapely, scipy, pandas, openpyxl, pymoo" >nul 2>&1
if errorlevel 1 (
  echo [RoomLight V5] Installing missing components...
  ".venv\Scripts\python.exe" -m pip install -r "room_light\requirements.txt" || goto :error
)
cd /d "%~dp0room_light"
"%~dp0.venv\Scripts\python.exe" main.py
if errorlevel 1 goto :runtime_error
exit /b 0

:runtime_error
echo.
echo RoomLight V5 stopped because of a startup error.
echo Check the newest file in room_light\logs, or send a screenshot of this window.
pause
exit /b 1

:error
echo.
echo RoomLight V5 could not create or repair its Python environment.
echo Python 3.10 or newer and an internet connection are required for first setup.
pause
exit /b 1
