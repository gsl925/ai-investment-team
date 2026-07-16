@echo off
setlocal
cd /d "%~dp0"

set "TASKNAME=InvestmentBackendAutoStart"

echo [Investment] Removing scheduled task "%TASKNAME%" ...
schtasks /delete /tn "%TASKNAME%" /f

if errorlevel 1 (
  echo.
  echo [Investment] Failed to remove scheduled task (it may not exist).
  if /i not "%1"=="nopause" pause
  exit /b 1
)

echo.
echo [Investment] Scheduled task "%TASKNAME%" removed.
echo Backend will no longer auto-start at logon.
echo Note: this does not stop a backend that is already running right now -
echo close its window or use Task Manager if you want to stop it immediately.
if /i not "%1"=="nopause" pause
