# =============================================================================
# Q-ALPHA — candidates/start_microstructure_logger.ps1
# Research-only READ-ONLY L2 + tape logger (clientId 72, paper 7497).
# Does not start Peak Hour / TSD. Does not place orders.
#
# Usage:
#   .\candidates\start_microstructure_logger.ps1
#   .\candidates\start_microstructure_logger.ps1 -Top 8 -AllowExtended
#   .\candidates\start_microstructure_logger.ps1 -Symbols "AAPL,MSFT" -Once
#   .\candidates\start_microstructure_logger.ps1 -Probe              # SPY only, max 3
#   .\candidates\start_microstructure_logger.ps1 -Probe -Symbols "SPY,AAPL"
# =============================================================================
param(
    [string]$Symbols = "",
    [int]$Top = 8,
    [switch]$AllowExtended,
    [switch]$Once,
    [switch]$Probe,
    [switch]$NoConnect,
    [double]$Seconds = 0
)

$ErrorActionPreference = "Continue"

$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $CandDir
Set-Location -LiteralPath $Root

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = $Root

$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "venv python not found at $Python"
    exit 1
}

$LogDir = Join-Path $CandDir "logs"
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$PidFile = Join-Path $LogDir "microstructure_logger.pid"

try {
    $etTz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $etNow = [System.TimeZoneInfo]::ConvertTime([datetime]::Now, $etTz)
    $etDate = $etNow.ToString("yyyy-MM-dd")
} catch {
    $etDate = Get-Date -Format "yyyy-MM-dd"
}
$LogFile = Join-Path $LogDir "microstructure_${etDate}.log"

if (Test-Path -LiteralPath $PidFile) {
    $oldPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($oldPid) {
        $alive = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
        if ($alive) {
            $msg = "REFUSE: microstructure logger already running (PID=$oldPid). Stop first."
            Write-Host $msg
            Add-Content -LiteralPath $LogFile -Value $msg
            exit 2
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

# L2 probes: few symbols only (1–3). Never Cap-scale depth at once.
$ProbeMax = 3
if ($Probe -or $Once) {
    if ($Top -gt $ProbeMax) { $Top = $ProbeMax }
    if (-not $Symbols) { $Symbols = "SPY" }
}

$argList = @(
    "-u",
    "-m",
    "candidates.microstructure_logger",
    "--top",
    "$Top"
)
if ($Symbols) { $argList += @("--symbols", $Symbols) }
if ($AllowExtended) { $argList += "--allow-extended" }
if ($Probe) { $argList += "--probe" }
elseif ($Once) { $argList += "--once" }
if ($NoConnect) { $argList += "--no-connect" }
if ($Seconds -gt 0) { $argList += @("--seconds", "$Seconds") }

$ErrFile = Join-Path $LogDir "microstructure_${etDate}.err.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
# Start-Process truncates redirect targets — do not prepend to $LogFile here.
$proc = Start-Process -FilePath $Python -ArgumentList $argList -WorkingDirectory $Root -PassThru -WindowStyle Hidden -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile
if (-not $proc) {
    Write-Error "failed to start microstructure logger"
    exit 1
}
Set-Content -LiteralPath $PidFile -Value $proc.Id -Encoding ascii
$meta = "START $stamp PID=$($proc.Id) clientId=72 paper 127.0.0.1:7497 READ-ONLY depth_source=PARTIAL_IEX_SMART"
Add-Content -LiteralPath $ErrFile -Value $meta
Write-Host "started microstructure logger PID=$($proc.Id) log=$LogFile pidfile=$PidFile"
exit 0
