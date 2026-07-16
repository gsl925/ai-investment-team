@echo off
setlocal
cd /d "%~dp0"

set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOGFILE=%LOGDIR%\startup_task.log"

echo ---------------------------------------- >> "%LOGFILE%"
echo [Investment] Startup task triggered at %date% %time% >> "%LOGFILE%"

call "%~dp0refresh_aegis_snapshot.bat" nopause >> "%LOGFILE%" 2>&1
if errorlevel 1 (
  echo [Investment] Warning: AegisTrader snapshot refresh failed, continuing with last snapshot. >> "%LOGFILE%"
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_backend_and_scheduler.ps1" >> "%LOGFILE%" 2>&1

echo [Investment] Startup task finished at %date% %time% >> "%LOGFILE%"
