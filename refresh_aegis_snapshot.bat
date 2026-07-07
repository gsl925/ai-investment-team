@echo off
setlocal
cd /d "%~dp0"

echo [Investment] Refreshing AegisTrader read-only snapshot ...
"%~dp0.venv\Scripts\python.exe" "%~dp0refresh_aegis_snapshot.py"

if errorlevel 1 (
  echo.
  echo [Investment] AegisTrader snapshot refresh failed.
  if /i not "%1"=="nopause" pause
  exit /b 1
)

echo [Investment] AegisTrader snapshot refresh complete.
if /i not "%1"=="nopause" pause
