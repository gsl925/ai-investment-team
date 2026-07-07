@echo off
setlocal
cd /d "%~dp0"

set SCAN_LIMIT=%1
set REFRESH_MINUTES=%2
set OFFSET=%3

if "%SCAN_LIMIT%"=="" set SCAN_LIMIT=100
if "%REFRESH_MINUTES%"=="" set REFRESH_MINUTES=60
if "%OFFSET%"=="" set OFFSET=0

echo [Investment] Full manual sync: Aegis + universe + ETF + scan
echo [Investment] Scan limit: %SCAN_LIMIT%  refresh_minutes: %REFRESH_MINUTES%  offset: %OFFSET%

call "%~dp0refresh_aegis_snapshot.bat" nopause
if errorlevel 1 (
  echo [Investment] Warning: Aegis snapshot refresh failed. Continuing.
)

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
  "Write-Host '[Investment] 1/5 Import TWSE listed stocks (官方上市清單) ...';" ^
  "try {" ^
  "  $twse=Invoke-RestMethod -Uri ($base + '/api/universe/import-twse') -TimeoutSec 60;" ^
  "  Write-Host '[Investment] TWSE rows:' $twse.row_count ' inserted:' $twse.inserted ' updated:' $twse.updated ' universe:' $twse.enabled_universe_count;" ^
  "} catch { Write-Host '[Investment] Warning: TWSE import failed (network?).' $_.Exception.Message };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] 2/5 Sync active ETF sources + import into universe ...';" ^
  "try {" ^
  "  $etf=Invoke-RestMethod -Uri ($base + '/api/active-etf/sync') -TimeoutSec 120;" ^
  "  Write-Host '[Investment] ETF source:' $etf.selected_source ' status:' $etf.status ' holdings:' $etf.source_status.latest_holding.holding_count ' changes:' $etf.source_status.latest_change.change_count;" ^
  "  $etfUniverse=Invoke-RestMethod -Uri ($base + '/api/universe/import-active-etf') -TimeoutSec 60;" ^
  "  Write-Host '[Investment] ETF universe inserted:' $etfUniverse.inserted ' updated:' $etfUniverse.updated ' universe:' $etfUniverse.enabled_universe_count;" ^
  "} catch { Write-Host '[Investment] Warning: ETF sync/universe import failed.' $_.Exception.Message };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] 3/5 Import universe_import.csv ...';" ^
  "try {" ^
  "  $csv=Invoke-RestMethod -Uri ($base + '/api/universe/import?file=universe_import.csv') -TimeoutSec 60;" ^
  "  Write-Host '[Investment] CSV inserted:' $csv.inserted ' updated:' $csv.updated ' universe:' $csv.enabled_universe_count;" ^
  "} catch { Write-Host '[Investment] Warning: CSV import failed.' $_.Exception.Message };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] 4/5 Scan market candidates ...';" ^
  "$scanUrl=$base + '/api/scan?limit=%SCAN_LIMIT%&min_priority=25&refresh_minutes=%REFRESH_MINUTES%&offset=%OFFSET%';" ^
  "$scan=Invoke-RestMethod -Uri $scanUrl -TimeoutSec 180;" ^
  "Write-Host '[Investment] Scan:' $scan.scan_run_id ' scanned:' $scan.scanned_count ' opportunities:' $scan.opportunities.Count ' cache:' $scan.cache_hits ' refreshed:' $scan.refreshed_count ' universe:' $scan.available_universe_count;" ^
  "if ($scan.exports.latest_markdown) { Write-Host '[Investment] Export:' $scan.exports.latest_markdown };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] 5/5 Final status ...';" ^
  "$status=Invoke-RestMethod -Uri ($base + '/api/db/status') -TimeoutSec 10;" ^
  "$tw=Invoke-RestMethod -Uri ($base + '/api/universe/tw-full-market/status') -TimeoutSec 10;" ^
  "Write-Host '[Investment] Universe:' $status.tables.universe ' snapshots:' $status.tables.price_snapshots ' ETF holdings:' $status.tables.active_etf_holdings ' ETF changes:' $status.tables.active_etf_changes;" ^
  "Write-Host '[Investment] Full market:' $tw.universe_count ' coverage:' ($tw.snapshot_coverage_percent.ToString() + '%%') ' no_quote:' $tw.no_quote_count;" ^
  "Write-Host '[Investment] Scheduler is NOT started by this batch. Use start_backend_and_scheduler.bat for scheduled runs.';" ^
  "Write-Host '[Investment] Open UI:' $base;"

if errorlevel 1 (
  echo.
  echo [Investment] Full manual sync failed.
  if /i not "%4"=="nopause" pause
  exit /b 1
)

echo.
echo [Investment] Full manual sync complete.
if /i not "%4"=="nopause" pause
