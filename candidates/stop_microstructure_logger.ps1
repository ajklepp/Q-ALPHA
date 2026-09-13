# =============================================================================
# Q-ALPHA — candidates/stop_microstructure_logger.ps1
# Stop the research-only microstructure logger (clientId 72).
# =============================================================================
$ErrorActionPreference = "Continue"

$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $CandDir
$LogDir = Join-Path $CandDir "logs"
$PidFile = Join-Path $LogDir "microstructure_logger.pid"

function Stop-LoggerPid {
    param([int]$ProcId)
    try {
        $p = Get-Process -Id $ProcId -ErrorAction Stop
        Write-Host "Stopping microstructure logger PID $ProcId ($($p.ProcessName))..."
        Stop-Process -Id $ProcId -Force
        return $true
    } catch {
        Write-Host "PID $ProcId already gone."
        return $false
    }
}

$stopped = $false
if (Test-Path -LiteralPath $PidFile) {
    $raw = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($raw) {
        $stopped = Stop-LoggerPid -ProcId ([int]$raw)
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

# Fallback: match the module invocation if PID file was stale.
$matches = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and ($_.CommandLine -match "candidates\.microstructure_logger")
})
foreach ($m in $matches) {
    Stop-LoggerPid -ProcId ([int]$m.ProcessId) | Out-Null
    $stopped = $true
}

if (-not $stopped) {
    Write-Host "Microstructure logger was not running."
    exit 0
}

Start-Sleep -Milliseconds 400
Write-Host "Microstructure logger stopped."
exit 0
