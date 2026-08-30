# =============================================================================
# Q-ALPHA — tools/sync_readonly_mirror.ps1
#
# Mirrors the editable repo to a sibling READ-ONLY copy for Cursor Chat A.
#
# WHY NOT SYMLINKS?
#   A symlink + attrib +R still writes through to the real file. Chat A could
#   corrupt Chat B's tree. This script uses a REAL copy, then marks it +R.
#
# Paths (sibling folders under Documents):
#   MAIN:     ...\Documents\Q-ALPHA              <- Chat B (edit)
#   READONLY: ...\Documents\Q-ALPHA-READONLY     <- Chat A (reference only)
#
# Usage:
#   .\tools\sync_readonly_mirror.ps1
#   .\tools\sync_readonly_mirror.ps1 -Watch   # FileSystemWatcher (blocks)
# =============================================================================
[CmdletBinding()]
param(
    [switch]$Watch,
    [int]$DebounceMs = 2500
)

$ErrorActionPreference = "Continue"

$MainRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $MainRoot "Q_ALPHA_HANDOFF.md"))) {
    $MainRoot = "C:\Users\ajkle\Documents\Q-ALPHA"
}
$ReadOnlyRoot = Join-Path (Split-Path -Parent $MainRoot) "Q-ALPHA-READONLY"

function Write-ReadOnlyGuardFiles {
    param([string]$Root)

    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
    $readme = @"
# Q-ALPHA - READ-ONLY MIRROR

Open this folder in Cursor for Chat A (reference / ask-only).
Open the sibling Q-ALPHA folder for Chat B (edits).

This tree is an automated mirror of the editable project. Do NOT edit here.
Changes made in Q-ALPHA are synced here by tools/sync_readonly_mirror.ps1.

- Synced from: $MainRoot
- Last sync: $stamp
- Files are marked OS read-only (attrib +R) after each sync.

If you need to change code, switch to the main Q-ALPHA workspace.
"@
    Set-Content -LiteralPath (Join-Path $Root "README_READONLY.md") -Value $readme -Encoding UTF8

    $rulesDir = Join-Path $Root ".cursor\rules"
    if (-not (Test-Path $rulesDir)) {
        New-Item -ItemType Directory -Path $rulesDir -Force | Out-Null
    }
    $rule = @"
---
description: This workspace is a READ-ONLY mirror - never edit files here
alwaysApply: true
---

# READ-ONLY WORKSPACE (Chat A)

You are in Q-ALPHA-READONLY, an auto-synced mirror of the editable repo.

## Absolute rules
1. Do not create, edit, delete, rename, or move any files in this workspace.
2. Do not run git commit / push / reset against this tree as if it were the main repo.
3. If the user asks for code changes, tell them to open the sibling folder Q-ALPHA (Chat B / Agent mode).
4. You MAY read files, search, explain, and answer questions.

Edits belong only in: $MainRoot
"@
    Set-Content -LiteralPath (Join-Path $rulesDir "read-only-mirror.mdc") -Value $rule -Encoding UTF8
}

function Sync-ReadOnlyMirror {
    Write-Host "[readonly-sync] MAIN      = $MainRoot"
    Write-Host "[readonly-sync] READONLY = $ReadOnlyRoot"

    if (-not (Test-Path -LiteralPath $MainRoot)) {
        Write-Error "Main repo not found: $MainRoot"
        return 1
    }
    if (-not (Test-Path -LiteralPath $ReadOnlyRoot)) {
        New-Item -ItemType Directory -Path $ReadOnlyRoot -Force | Out-Null
    }

    Write-Host "[readonly-sync] clearing read-only attributes..."
    attrib -R "$ReadOnlyRoot\*.*" /S /D 2>$null

    $excludeDirs = @(
        "venv", ".venv", "node_modules", "__pycache__", ".pytest_cache",
        ".mypy_cache", "logs", "strategy_lab\logs", "strategy_lab\results\bars",
        "strategy_lab\results\daily_cache", "strategy_lab\profiles",
        ".git"
    )
    $xdArgs = @()
    foreach ($d in $excludeDirs) { $xdArgs += @("/XD", $d) }

    Write-Host "[readonly-sync] robocopy /MIR ..."
    & robocopy $MainRoot $ReadOnlyRoot /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP `
        /XF "*.pyc" "*.pyo" ".env" `
        @xdArgs | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
        Write-Warning "robocopy failed with exit $rc"
        return $rc
    }

    Write-ReadOnlyGuardFiles -Root $ReadOnlyRoot

    Write-Host "[readonly-sync] marking mirror +R ..."
    attrib +R "$ReadOnlyRoot\*.*" /S /D 2>$null

    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[readonly-sync] done at $stamp (robocopy=$rc)"
    return 0
}

if ($Watch) {
    Write-Host "[readonly-sync] initial sync..."
    [void](Sync-ReadOnlyMirror)

    Write-Host "[readonly-sync] watching $MainRoot (debounce ${DebounceMs}ms). Ctrl+C to stop."
    $timer = New-Object System.Timers.Timer
    $timer.Interval = $DebounceMs
    $timer.AutoReset = $false
    $script:pending = $false

    $handler = {
        $script:pending = $true
        $timer.Stop()
        $timer.Start()
    }
    $elapsed = {
        if ($script:pending) {
            $script:pending = $false
            Write-Host "[readonly-sync] change detected - syncing..."
            [void](Sync-ReadOnlyMirror)
        }
    }

    Register-ObjectEvent -InputObject $timer -EventName Elapsed -Action $elapsed | Out-Null

    $fsw = New-Object System.IO.FileSystemWatcher $MainRoot
    $fsw.IncludeSubdirectories = $true
    $fsw.EnableRaisingEvents = $true
    foreach ($ev in @("Changed", "Created", "Deleted", "Renamed")) {
        Register-ObjectEvent -InputObject $fsw -EventName $ev -Action $handler | Out-Null
    }

    while ($true) { Start-Sleep -Seconds 3600 }
} else {
    exit (Sync-ReadOnlyMirror)
}
