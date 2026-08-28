# =============================================================================
# Q-ALPHA - start_dashboard.ps1
# Launch Streamlit DETACHED so the caller returns immediately.
#
# Usage (from repo root):
#   .\start_dashboard.ps1
#   .\start_dashboard.ps1 -Port 8501
#
# URL: http://localhost:<Port>
# Stop: .\stop_dashboard.ps1
# =============================================================================
param(
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "venv python not found at $Python - create/activate venv first."
}

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $pids = ($existing.OwningProcess | Sort-Object -Unique) -join ", "
    Write-Host "Dashboard already listening on port $Port (PID(s): $pids)"
    Write-Host "URL: http://localhost:$Port"
    Write-Host "Stop with: .\stop_dashboard.ps1 -Port $Port"
    exit 0
}

$LogDir = Join-Path $Root "candidates"
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}
$OutLog = Join-Path $LogDir "dashboard_stdout.log"
$ErrLog = Join-Path $LogDir "dashboard_stderr.log"

$argList = @(
    "-m", "streamlit", "run", "dashboard.py",
    "--server.port", "$Port",
    "--server.headless", "true"
)

$proc = Start-Process -FilePath $Python `
    -ArgumentList $argList `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

Write-Host "Dashboard started DETACHED (PID $($proc.Id))"
Write-Host "URL: http://localhost:$Port"
Write-Host "Stdout log: $OutLog"
Write-Host "Stderr log: $ErrLog"
Write-Host ('Stop: {0} -Port {1}' -f (Join-Path $Root 'stop_dashboard.ps1'), $Port)
