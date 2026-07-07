@echo off
setlocal
cd /d "%~dp0"

set YAHOO_COUNT=%1
if "%YAHOO_COUNT%"=="" set YAHOO_COUNT=250

echo [Investment] Build candidate universe only
echo [Investment] This updates symbol lists only. It does not scan prices/news/snapshots.
echo [Investment] Yahoo count/screener: %YAHOO_COUNT%

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
  "Write-Host '[Investment] 1/5 TWSE official listed market ...';" ^
  "try {" ^
  "  $twse=Invoke-RestMethod -Uri ($base + '/api/universe/import-twse') -TimeoutSec 60;" ^
  "  Write-Host '[Investment] TWSE rows:' $twse.row_count ' inserted:' $twse.inserted ' updated:' $twse.updated ' universe:' $twse.enabled_universe_count;" ^
  "} catch { Write-Host '[Investment] Warning: TWSE import failed (network?).' $_.Exception.Message };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] 2/5 Active ETF holdings source ...';" ^
  "try {" ^
  "  $etfSync=Invoke-RestMethod -Uri ($base + '/api/active-etf/sync') -TimeoutSec 120;" ^
  "  $etfUniverse=Invoke-RestMethod -Uri ($base + '/api/universe/import-active-etf') -TimeoutSec 60;" ^
  "  Write-Host '[Investment] ETF holdings:' $etfSync.source_status.latest_holding.holding_count ' universe inserted:' $etfUniverse.inserted ' updated:' $etfUniverse.updated ' universe:' $etfUniverse.enabled_universe_count;" ^
  "} catch { Write-Host '[Investment] Warning: ETF sync/universe import failed.' $_.Exception.Message };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] 3/5 Local universe_import.csv ...';" ^
  "try {" ^
  "  $csv=Invoke-RestMethod -Uri ($base + '/api/universe/import?file=universe_import.csv') -TimeoutSec 60;" ^
  "  Write-Host '[Investment] CSV inserted:' $csv.inserted ' updated:' $csv.updated ' universe:' $csv.enabled_universe_count;" ^
  "} catch { Write-Host '[Investment] Warning: CSV import failed.' $_.Exception.Message };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] 4/5 Yahoo screeners broad discovery ...';" ^
  "try {" ^
  "  $yahoo=Invoke-RestMethod -Uri ($base + '/api/universe/maximize?count=%YAHOO_COUNT%') -TimeoutSec 180;" ^
  "  Write-Host '[Investment] Yahoo inserted:' $yahoo.inserted ' updated:' $yahoo.updated ' skipped:' $yahoo.skipped ' universe:' $yahoo.enabled_universe_count;" ^
  "  if ($yahoo.errors.Count -gt 0) { Write-Host '[Investment] Yahoo errors:' ($yahoo.errors | ConvertTo-Json -Compress) };" ^
  "} catch { Write-Host '[Investment] Warning: Yahoo maximize failed.' $_.Exception.Message };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] 5/5 Final status ...';" ^
  "$status=Invoke-RestMethod -Uri ($base + '/api/db/status') -TimeoutSec 10;" ^
  "Write-Host '[Investment] Universe:' $status.tables.universe;" ^
  "Write-Host '[Investment] Snapshots unchanged (use sync_all_now.bat or start_backend_and_scheduler.bat to scan):' $status.tables.price_snapshots;" ^
  "Write-Host '[Investment] Scheduler is not started by this batch.';" ^
  "Write-Host '[Investment] Open UI:' $base;"

if errorlevel 1 (
  echo.
  echo [Investment] Build universe failed.
  if /i not "%2"=="nopause" pause
  exit /b 1
)

echo.
echo [Investment] Candidate universe build complete.
if /i not "%2"=="nopause" pause
