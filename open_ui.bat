@echo off
setlocal
cd /d "%~dp0"

set BASE=http://127.0.0.1:8765

echo [Investment] Checking backend ...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$base='%BASE%';" ^
  "$alive=$false;" ^
  "try { Invoke-RestMethod -Uri ($base+'/api/db/status') -TimeoutSec 3 | Out-Null; $alive=$true } catch {};" ^
  "if ($alive) {" ^
  "  Write-Host '[Investment] Server already running. Opening UI ...';" ^
  "  Start-Process $base;" ^
  "  exit 0" ^
  "};" ^
  "Write-Host '[Investment] Server not running. Starting venv python app.py ...';" ^
  "$root=(Get-Location).Path;" ^
  "Start-Process -FilePath ""$root\.venv\Scripts\python.exe"" -ArgumentList 'app.py' -WorkingDirectory $root -WindowStyle Hidden;" ^
  "Write-Host '[Investment] Waiting for server to be ready ...';" ^
  "$serverAlive=$false;" ^
  "for ($i=0; $i -lt 40; $i++) {" ^
  "  Start-Sleep -Seconds 1;" ^
  "  try { Invoke-RestMethod -Uri ($base+'/api/db/status') -TimeoutSec 2 | Out-Null; $serverAlive=$true; break } catch {}" ^
  "};" ^
  "if (-not $serverAlive) { Write-Host '[Investment] ERROR: Server did not start within 40s.'; exit 1 };" ^
  "Write-Host '[Investment] Server ready. Opening UI ...';" ^
  "Start-Process $base;"

if errorlevel 1 (
  echo.
  echo [Investment] Failed to start server.
  echo Try running start_backend_and_scheduler.bat instead.
  pause
  exit /b 1
)

echo.
echo [Investment] UI opened: %BASE%
echo.
if not "%1"=="nopause" pause
