# Stop the Workflow Metadata API: kill anything bound to port 8000 plus
# every python process on the machine. Use whenever you're done working on
# the project to make sure no orphan uvicorn workers are squatting on the
# port (a common source of "stale routes" 404s).
#
# Usage:
#   pwsh -File scripts/stop.ps1
#   # or, from inside an open PowerShell session at the project root:
#   .\scripts\stop.ps1

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$KeepPython     # skip the broad "kill all python.exe" sweep
)

$ErrorActionPreference = "Continue"

Write-Host "--- Stopping Workflow Metadata API ---" -ForegroundColor Cyan

# 1. Kill anything bound to the listen port.
$listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listen) {
    foreach ($conn in $listen) {
        $pidToKill = $conn.OwningProcess
        try {
            Stop-Process -Id $pidToKill -Force -ErrorAction Stop
            Write-Host "  killed PID $pidToKill (port $Port)" -ForegroundColor Yellow
        } catch {
            Write-Host "  could not kill PID $pidToKill : $($_.Exception.Message)" -ForegroundColor Red
        }
    }
} else {
    Write-Host "  port $Port already free" -ForegroundColor Gray
}

# 2. Sweep python processes (broad — covers reload subprocesses + debugpy targets).
if (-not $KeepPython) {
    $py = Get-Process python, pythonw -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($proc in $py) {
            try {
                Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                Write-Host "  killed python PID $($proc.Id)" -ForegroundColor Yellow
            } catch {
                Write-Host "  could not kill python PID $($proc.Id)" -ForegroundColor Red
            }
        }
    } else {
        Write-Host "  no python processes were running" -ForegroundColor Gray
    }
}

Start-Sleep -Seconds 1

# 3. Report.
$still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
$pyAfter = if ($KeepPython) { @() } else { Get-Process python, pythonw -ErrorAction SilentlyContinue }

Write-Host "--- Done ---" -ForegroundColor Cyan
if ($still) {
    Write-Host "  port $Port STILL bound by PID $($still.OwningProcess)" -ForegroundColor Red
} else {
    Write-Host "  port $Port free" -ForegroundColor Green
}
if (-not $KeepPython) {
    if ($pyAfter) {
        Write-Host "  $($pyAfter.Count) python process(es) still running" -ForegroundColor Red
    } else {
        Write-Host "  no python processes" -ForegroundColor Green
    }
}
