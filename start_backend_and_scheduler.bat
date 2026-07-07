@echo off
setlocal
cd /d "%~dp0"

call "%~dp0refresh_aegis_snapshot.bat" nopause
if errorlevel 1 (
  echo.
  echo [Investment] Warning: failed to refresh AegisTrader snapshot. Continuing with the last local snapshot.
)

echo [Investment] Starting backend (fresh restart) ...

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_backend_and_scheduler.ps1"

if errorlevel 1 (
  echo.
  echo [Investment] Failed to start backend or scheduler.
)

echo.
echo [Investment] Backend and scheduler are running.
echo Open UI: http://127.0.0.1:8765
echo.
cmd /k
