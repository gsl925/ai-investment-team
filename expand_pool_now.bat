@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set SCREENER_COUNT=%1
set SCAN_LIMIT=%2
set ROUNDS=%3
set REFRESH_MINUTES=%4

if "%SCREENER_COUNT%"=="" set SCREENER_COUNT=250
if "%SCAN_LIMIT%"=="" set SCAN_LIMIT=100
if "%ROUNDS%"=="" set ROUNDS=10
if "%REFRESH_MINUTES%"=="" set REFRESH_MINUTES=1440

echo [Investment] Concentrated universe expansion
echo [Investment] Yahoo count/screener: %SCREENER_COUNT%  scan_limit: %SCAN_LIMIT%  rounds: %ROUNDS%  refresh_minutes: %REFRESH_MINUTES%

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
  "Write-Host '[Investment] Step 1/5: Sync active ETF source ...';" ^
  "try {" ^
  "  $etf=Invoke-RestMethod -Uri ($base + '/api/active-etf/sync') -TimeoutSec 120;" ^
  "  Write-Host '[Investment] ETF source:' $etf.selected_source ' holdings:' $etf.source_status.latest_holding.holding_count ' changes:' $etf.source_status.latest_change.change_count;" ^
  "} catch { Write-Host '[Investment] Warning: ETF sync failed.' $_.Exception.Message };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] Step 2/5: Import ETF holdings into universe ...';" ^
  "$etfUniverse=Invoke-RestMethod -Uri ($base + '/api/universe/import-active-etf') -TimeoutSec 60;" ^
  "Write-Host '[Investment] ETF universe inserted:' $etfUniverse.inserted ' updated:' $etfUniverse.updated ' universe:' $etfUniverse.enabled_universe_count;" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] Step 3/5: Import local universe_import.csv ...';" ^
  "$csv=Invoke-RestMethod -Uri ($base + '/api/universe/import?file=universe_import.csv') -TimeoutSec 60;" ^
  "Write-Host '[Investment] CSV inserted:' $csv.inserted ' updated:' $csv.updated ' universe:' $csv.enabled_universe_count;" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] Step 4/5: Maximize from Yahoo screeners ...';" ^
  "try {" ^
  "  $max=Invoke-RestMethod -Uri ($base + '/api/universe/maximize?count=%SCREENER_COUNT%') -TimeoutSec 180;" ^
  "  Write-Host '[Investment] Yahoo inserted:' $max.inserted ' updated:' $max.updated ' skipped:' $max.skipped ' universe:' $max.enabled_universe_count;" ^
  "  if ($max.errors.Count -gt 0) { Write-Host '[Investment] Yahoo errors:' ($max.errors | ConvertTo-Json -Compress) };" ^
  "} catch { Write-Host '[Investment] Warning: Yahoo maximize failed.' $_.Exception.Message };" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] Step 5/5: Batch scan expanded universe ...';" ^
  "$offset=0;" ^
  "for ($r=1; $r -le %ROUNDS%; $r++) {" ^
  "  $scanUrl=$base + '/api/scan?limit=%SCAN_LIMIT%&min_priority=25&refresh_minutes=%REFRESH_MINUTES%&offset=' + $offset;" ^
  "  $scan=Invoke-RestMethod -Uri $scanUrl -TimeoutSec 240;" ^
  "  Write-Host ('[Investment] Round {0}/{1}: scan #{2} offset {3} scanned {4} refreshed {5} cache {6} opp {7}' -f $r,%ROUNDS%,$scan.scan_run_id,$offset,$scan.scanned_count,$scan.refreshed_count,$scan.cache_hits,$scan.opportunities.Count);" ^
  "  $offset += %SCAN_LIMIT%;" ^
  "  if ($offset -ge $scan.available_universe_count) { $offset=0 }" ^
  "}" ^
  "Write-Host '';" ^
  "$status=Invoke-RestMethod -Uri ($base + '/api/db/status') -TimeoutSec 10;" ^
  "Write-Host '[Investment] Final universe:' $status.tables.universe ' snapshots:' $status.tables.price_snapshots ' scans:' $status.tables.scan_runs ' opportunities:' $status.tables.opportunities;" ^
  "Write-Host '[Investment] Backend remains running. Scheduler is not started by this batch.';" ^
  "Write-Host '[Investment] Open UI:' $base;"

if errorlevel 1 (
  echo.
  echo [Investment] Universe expansion failed.
  if /i not "%5"=="nopause" pause
  exit /b 1
)

echo.
echo [Investment] Concentrated universe expansion complete.
if /i not "%5"=="nopause" pause
