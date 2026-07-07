@echo off
setlocal
cd /d "%~dp0"

echo [Investment] Stopping background scheduler only ...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$base='http://127.0.0.1:8765';" ^
  "try { $status=Invoke-RestMethod -Uri ($base + '/api/scheduler/stop') -TimeoutSec 10 } catch { throw 'Backend is not reachable at http://127.0.0.1:8765. If the backend is already closed, the scheduler is also stopped.' };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] Backend URL:' $base;" ^
  "Write-Host '[Investment] Scheduler enabled:' $status.enabled;" ^
  "Write-Host '[Investment] Running now:' $status.running;" ^
  "Write-Host '[Investment] Thread alive:' $status.thread_alive;" ^
  "Write-Host '[Investment] Next run UTC:' $status.next_run_at;" ^
  "Write-Host '[Investment] Backend remains running. Use stop_backend.bat if you want to close the UI server too.';"

if errorlevel 1 (
  echo.
  echo [Investment] Failed to stop scheduler.
  if /i not "%1"=="nopause" pause
  exit /b 1
)

echo.
echo [Investment] Background scheduler stopped.
if /i not "%1"=="nopause" pause
