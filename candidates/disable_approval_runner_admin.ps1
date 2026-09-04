# =============================================================================
# Q-ALPHA — candidates/disable_approval_runner_admin.ps1
# Disables "QAlpha Approval Runner" (needs Administrator — UAC prompt).
# Usage: right-click → Run with PowerShell as Admin, or:
#   Start-Process powershell -Verb RunAs -ArgumentList '-File', $PSCommandPath
# =============================================================================
$ErrorActionPreference = "Continue"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "Elevating to Administrator..."
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath
    ) | Out-Null
    exit 0
}

schtasks /Change /TN "QAlpha Approval Runner" /DISABLE
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: QAlpha Approval Runner DISABLED"
} else {
    Write-Error "Failed to disable QAlpha Approval Runner (exit $LASTEXITCODE)"
    exit 1
}
schtasks /Query /TN "QAlpha Approval Runner" /FO LIST | Select-String -Pattern "Status|TaskName|Next Run"
