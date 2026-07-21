param(
    [int]$BatchSize = 250,
    [int]$Loops = 10,
    [string]$BaseUrl = 'http://127.0.0.1:8765'
)

# Runs as a detached background process from start_backend_and_scheduler.ps1 (-File, not
# -Command with a hand-built string) so PowerShell handles argument quoting itself — the
# previous inline "for (...) { ... }" string built with backticks/quotes was suspected of
# failing to parse correctly under Start-Process -ArgumentList, so this scans got silently
# cut short. Logs each batch so a partial run is visible instead of invisible.

$logPath = Join-Path $PSScriptRoot 'logs\catchup_scan.log'
$logDir = Split-Path $logPath -Parent
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

function Write-CatchupLog($message) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $message"
    Add-Content -Path $logPath -Value $line
}

Write-CatchupLog "Catch-up started: $Loops x $BatchSize batches, base=$BaseUrl"

for ($i = 0; $i -lt $Loops; $i++) {
    $offset = $i * $BatchSize
    $uri = "$BaseUrl/api/scan?scope=tw_full_market&limit=$BatchSize&refresh_minutes=0&offset=$offset"
    try {
        Invoke-RestMethod -Uri $uri -TimeoutSec 400 | Out-Null
        Write-CatchupLog "Batch $($i + 1)/$Loops offset=$offset OK"
    } catch {
        Write-CatchupLog "Batch $($i + 1)/$Loops offset=$offset FAILED: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 2
}

Write-CatchupLog "Catch-up finished: $Loops batches attempted."
