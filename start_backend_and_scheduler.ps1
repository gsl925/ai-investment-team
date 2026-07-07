param()
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:8765'
$root = $PSScriptRoot

# --- Kill existing backend ---
$existing = Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue
if ($existing) {
    $oldProcId = $existing[0].OwningProcess
    Write-Host "[Investment] Stopping existing backend (PID $oldProcId) ..."
    Stop-Process -Id $oldProcId -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# --- Launch new backend ---
$venvPython = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "venv not found at $venvPython — run: py -3.14 -m venv .venv && .venv\Scripts\pip install -r requirements.txt" }
Write-Host "[Investment] Launching $venvPython app.py ..."
Start-Process -FilePath $venvPython -ArgumentList 'app.py' -WorkingDirectory $root -WindowStyle Hidden

# --- Wait for server to be ready (up to 60s) ---
$serverAlive = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    try {
        Invoke-RestMethod -Uri ($base + '/api/db/status') -TimeoutSec 3 | Out-Null
        $serverAlive = $true
        break
    } catch { }
}
if (-not $serverAlive) {
    throw "Backend did not become ready on port 8765 after 60 seconds."
}
Write-Host "[Investment] Backend ready."

# --- Start scheduler (retry up to 3 times) ---
$schedulerOk = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
        $status = Invoke-RestMethod -Uri ($base + '/api/scheduler/start?interval_minutes=30&batch_size=25&refresh_minutes=60&min_priority=25&scope=tw_full_market') -TimeoutSec 10
        Write-Host "[Investment] Scheduler started: enabled=$($status.enabled) scope=$($status.scope)"
        $schedulerOk = $true
        break
    } catch {
        Write-Host "[Investment] Scheduler start attempt $attempt failed: $($_.Exception.Message)"
        if ($_.Exception.Response) {
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            Write-Host $reader.ReadToEnd()
        }
        if ($attempt -lt 3) {
            Write-Host "[Investment] Retrying in 5s..."
            Start-Sleep -Seconds 5
        }
    }
}
if (-not $schedulerOk) {
    throw "Scheduler failed to start after 3 attempts."
}

# --- Verify scheduler thread is alive ---
Start-Sleep -Seconds 2
$scheduler = Invoke-RestMethod -Uri ($base + '/api/scheduler/status') -TimeoutSec 10
if (-not $scheduler.thread_alive) {
    throw "Scheduler thread is not alive after start. last_error=$($scheduler.last_error)"
}

# --- Print status summary ---
$latest = Invoke-RestMethod -Uri ($base + '/api/dashboard/latest') -TimeoutSec 10
$tw     = Invoke-RestMethod -Uri ($base + '/api/universe/tw-full-market/status') -TimeoutSec 10

Write-Host ""
Write-Host "[Investment] Backend URL: $base"
Write-Host "[Investment] Scheduler enabled: $($scheduler.enabled)  healthy: $($scheduler.healthy)  thread_alive: $($scheduler.thread_alive)  scope: $($scheduler.scope)"
Write-Host "[Investment] Latest scan: $($latest.latest_scan.id)  scanned: $($latest.latest_scan.scanned_count)  opportunities: $($latest.latest_scan.opportunity_count)"
Write-Host "[Investment] Full market universe: $($tw.universe_count)  snapshot coverage: $($tw.snapshot_coverage_percent)%%  no_quote: $($tw.no_quote_count)"
Write-Host "[Investment] Next run UTC: $($scheduler.next_run_at)"
Write-Host "[Investment] Open UI: $base"

# --- Catch-up scan if >70% stale ---
$needsCatchup = $false
try {
    $totalSnaps = [int]($latest.coverage.snapshot_symbol_count)
    $freshSnaps = [int]($latest.coverage.updated_24h_count)
    if ($totalSnaps -gt 0) {
        $staleRatio = ($totalSnaps - $freshSnaps) / $totalSnaps
        Write-Host ("[Investment] Snapshot freshness: $freshSnaps/$totalSnaps updated in 24h (" + [math]::Round((1 - $staleRatio) * 100, 1) + "% fresh).")
        if ($staleRatio -gt 0.7) {
            $needsCatchup = $true
            Write-Host "[Investment] >70% stale — catch-up scan will run in background."
        }
    }
} catch { $needsCatchup = $true }

if ($needsCatchup) {
    $catchupScript = 'for ($i=0; $i -lt 10; $i++) { try { Invoke-RestMethod -Uri ''http://127.0.0.1:8765/api/scan?scope=tw_full_market&limit=250&refresh_minutes=0'' -TimeoutSec 180 | Out-Null } catch { }; Start-Sleep -Seconds 2 }'
    Start-Process powershell -ArgumentList "-NoProfile -WindowStyle Hidden -Command $catchupScript" -WindowStyle Hidden
    Write-Host "[Investment] Catch-up: 10x250 scans running in background (covers all 2454 symbols, ~15 min)."
}
