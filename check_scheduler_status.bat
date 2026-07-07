@echo off
setlocal
cd /d "%~dp0"

echo [Investment] Checking scheduler status ...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$base='http://127.0.0.1:8765';" ^
  "try { $latest=Invoke-RestMethod -Uri ($base + '/api/dashboard/latest') -TimeoutSec 10 } catch { throw 'Backend is not reachable at http://127.0.0.1:8765. Run start_backend_and_scheduler.bat first.' };" ^
  "$s=$latest.scheduler; $c=$latest.coverage; $scan=$latest.latest_scan;" ^
  "Write-Host '';" ^
  "Write-Host '[Investment] Backend URL:' $base;" ^
  "Write-Host '[Investment] Scheduler enabled:' $s.enabled;" ^
  "Write-Host '[Investment] Running now:' $s.running;" ^
  "Write-Host '[Investment] Thread alive:' $s.thread_alive;" ^
  "Write-Host '[Investment] Healthy:' $s.healthy;" ^
  "Write-Host '[Investment] Overdue seconds:' $s.overdue_seconds;" ^
  "Write-Host '[Investment] Last run UTC:' $s.last_run_at;" ^
  "Write-Host '[Investment] Next run UTC:' $s.next_run_at;" ^
  "Write-Host '[Investment] Latest scan:' $scan.id;" ^
  "Write-Host '[Investment] Scanned:' $scan.scanned_count ' Opportunities:' $scan.opportunity_count ' Cache hits:' $scan.cache_hits ' Refreshed:' $scan.refreshed_count;" ^
  "Write-Host '[Investment] Universe:' $c.universe_count ' Snapshot symbols:' $c.snapshot_symbol_count ' Updated 24h:' $c.updated_24h_count ' Coverage:' ($c.snapshot_coverage_percent.ToString() + '%%');" ^
  "if ($s.healthy -ne $true) { Write-Host '[Investment] WARNING: scheduler is not healthy. Calling /api/scheduler/status can auto-repair stale state.'; Invoke-RestMethod -Uri ($base + '/api/scheduler/status') -TimeoutSec 10 | Out-Null }"

if errorlevel 1 (
  echo.
  echo [Investment] Status check failed.
  if /i not "%1"=="nopause" pause
  exit /b 1
)

echo.
echo [Investment] Status check complete.
if /i not "%1"=="nopause" pause
