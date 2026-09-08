# =============================================================================
# Q-ALPHA - candidates/register_approval_runner_admin.ps1
# Re-register ONLY "QAlpha Approval Runner" (requires Administrator).
#
# Right-click PowerShell -> Run as administrator, then:
#   cd C:\Users\ajkle\Documents\Q-ALPHA
#   .\candidates\register_approval_runner_admin.ps1
# =============================================================================
$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "ERROR: This script must run in an elevated (Administrator) PowerShell window."
    Write-Host "Right-click PowerShell -> Run as administrator, then re-run this script."
    exit 1
}

$CandDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApprovalPs1 = Join-Path $CandDir "start_approval_runner_scheduled.ps1"
if (-not (Test-Path -LiteralPath $ApprovalPs1)) {
    Write-Error "Missing: $ApprovalPs1"
}

$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ApprovalPs1"

# Remove locked legacy task (OneDrive paths), then recreate.
schtasks /Delete /TN "QAlpha Approval Runner" /F 2>&1 | ForEach-Object { Write-Host $_ }
schtasks /Create /F /TN "QAlpha Approval Runner" /TR $tr /SC DAILY /ST 09:25 /RL LIMITED
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to create QAlpha Approval Runner"
}

Write-Host ""
Write-Host "QAlpha Approval Runner : OK"
Write-Host ""
schtasks /Query /TN "QAlpha Approval Runner" /XML | Select-String -Pattern 'Command>|Arguments>'
