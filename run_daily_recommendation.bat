@echo off
setlocal
cd /d "%~dp0"

set FORCE=%1
set LIMIT=%2

if "%FORCE%"=="" set FORCE=1
if "%LIMIT%"=="" set LIMIT=50

echo [Investment] Run daily recommendation log  force=%FORCE%  limit=%LIMIT%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$base='http://127.0.0.1:8765';" ^
  "$root=(Get-Location).Path;" ^
  "$serverAlive=$false;" ^
  "try { Invoke-RestMethod -Uri ($base + '/api/db/status') -TimeoutSec 3 | Out-Null; $serverAlive=$true } catch { $serverAlive=$false };" ^
  "if (-not $serverAlive) {" ^
  "  Write-Host '[Investment] Backend not running. Starting venv python app.py ...';" ^
  "  Start-Process -FilePath ""$root\.venv\Scripts\python.exe"" -ArgumentList 'app.py' -WorkingDirectory $root -WindowStyle Hidden;" ^
  "  for ($i=0; $i -lt 20; $i++) {" ^
  "    Start-Sleep -Seconds 1;" ^
  "    try { Invoke-RestMethod -Uri ($base + '/api/db/status') -TimeoutSec 3 | Out-Null; $serverAlive=$true; break } catch { }" ^
  "  }" ^
  "} else { Write-Host '[Investment] Backend already running.' };" ^
  "if (-not $serverAlive) { throw 'Backend did not become ready on port 8765.' };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] Calling /api/recommendations/daily-log?force=%FORCE%&limit=%LIMIT% ...';" ^
  "$url=$base + '/api/recommendations/daily-log?force=%FORCE%&limit=%LIMIT%';" ^
  "$r=Invoke-RestMethod -Uri $url -TimeoutSec 120;" ^
  "Write-Host '';" ^
  "if ($r.ran -eq $false) {" ^
  "  Write-Host '[Investment] Skipped:' $r.reason;" ^
  "  Write-Host '[Investment] To force re-run: run_daily_recommendation.bat 1';" ^
  "} else {" ^
  "  Write-Host '[Investment] Done! date:' $r.date ' recommendations:' $r.recommendation_count;" ^
  "  if ($r.markdown_path) { Write-Host '[Investment] Markdown:' $r.markdown_path };" ^
  "  if ($r.jsonl_path)    { Write-Host '[Investment] JSONL:   ' $r.jsonl_path };" ^
  "  if ($r.note)          { Write-Host '[Investment] Note:   ' $r.note };" ^
  "}"

if errorlevel 1 (
  echo.
  echo [Investment] Daily recommendation failed.
  pause
  exit /b 1
)

echo.
echo [Investment] Complete.
pause
