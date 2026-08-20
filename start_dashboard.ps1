# =============================================================================
# Q-ALPHA — start_dashboard.ps1
# Launch Streamlit DETACHED so the caller returns immediately.
#
# WHY: `streamlit run` never exits. Running it in the foreground freezes Cursor
# (and any agent) waiting on the process. Always use this script (or the
# Start-Process one-liner below) — never `streamlit run` as a blocking command.
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
Set-Location $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "venv python not found at $Python — create/activate venv first."
}

# Refuse to start a second copy on the same port.
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Dashboard already listening on port $Port (PID(s): $($existing.OwningProcess -join ', '))"
    Write-Host "URL: http://localhost:$Port"
    Write-Host "Stop with: .\stop_dashboard.ps1 -Port $Port"
    exit 0
}

$LogDir = Join-Path $Root "candidates"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$OutLog = Join-Path $LogDir "dashboard_stdout.log"
$ErrLog = Join-Path $LogDir "dashboard_stderr.log"

# Detached: new process, no wait. Caller returns immediately.
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
Write-Host "Logs: $OutLog | $ErrLog"
Write-Host "Stop: .\stop_dashboard.ps1 -Port $Port"
