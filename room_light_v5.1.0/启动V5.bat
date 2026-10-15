@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [RoomLight V5] 首次运行，正在建立独立环境并安装依赖...
  py -3 -m venv ".venv" || goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
  ".venv\Scripts\python.exe" -m pip install -r "room_light\requirements.txt" || goto :error
)
cd /d "%~dp0room_light"
"%~dp0.venv\Scripts\python.exe" main.py
exit /b %errorlevel%

:error
echo.
echo RoomLight V5 启动环境建立失败。请确认已安装 Python 3.10 或更高版本并可联网安装依赖。
pause
exit /b 1
