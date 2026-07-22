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

# 2026-07-22: Add-Content re-opens+closes the file on every call, and something on this
# machine (Defender real-time scan is the leading suspect - MsMpEng was observed running)
# re-locks the file on open often enough that most lines after the first were silently
# dropped (verified: batches 2/3 and the finish line all missed the main log in two
# consecutive test runs). Fix: open one FileStream for the whole script run with
# FileShare.ReadWrite so concurrent readers (tail, Get-Content) don't block us and we
# don't pay the re-open cost per line. Retry+fallback kept as a last-resort safety net.
$logStream = [System.IO.FileStream]::new($logPath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
$logWriter = [System.IO.StreamWriter]::new($logStream)
$logWriter.AutoFlush = $true

function Write-CatchupLog($message) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $message"
    try {
        $logWriter.WriteLine($line)
        return
    } catch {
        # fall through to retry against the shared handle failing entirely
    }
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            Add-Content -Path $logPath -Value $line -ErrorAction Stop
            return
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }
    try {
        Add-Content -Path "$logPath.fallback" -Value $line -ErrorAction Stop
    } catch {}
}

try {
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
} finally {
    $logWriter.Close()
    $logStream.Close()
}
