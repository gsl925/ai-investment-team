@echo off
setlocal
cd /d "%~dp0"

set "TASKNAME=InvestmentBackendAutoStart"
set "TARGET=%~dp0run_startup_task.bat"

echo [Investment] Registering scheduled task "%TASKNAME%" ...
echo   Trigger: at user logon
echo   Action:  "%TARGET%"
echo.

schtasks /create /tn "%TASKNAME%" /tr "\"%TARGET%\"" /sc onlogon /f

if errorlevel 1 (
  echo.
  echo [Investment] Failed to register scheduled task.
  echo If this keeps failing, try right-click this file - "Run as administrator".
  if /i not "%1"=="nopause" pause
  exit /b 1
)

echo.
echo [Investment] Scheduled task "%TASKNAME%" registered.
echo Backend + scheduler will now auto-start every time you log in to Windows.
echo Startup log: %~dp0logs\startup_task.log
echo.
echo To remove it later, run unregister_startup_task.bat
if /i not "%1"=="nopause" pause
