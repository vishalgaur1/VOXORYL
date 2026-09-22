#Requires -Version 5.1
<#
.SYNOPSIS
  VOXORYL install entry (Windows).

.DESCRIPTION
  Prefers the Setup Wizard (scripts/setup_voxoryl.py). Pass -Cli to skip the GUI
  and run scripts/bootstrap_voxoryl.py directly (CI / headless).
#>
param(
  [switch]$Cli
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> VOXORYL install (user data: $env:LOCALAPPDATA\VOXORYL)" -ForegroundColor Cyan

$py = $null
foreach ($cand in @("python", "py")) {
  if (Get-Command $cand -ErrorAction SilentlyContinue) {
    $py = $cand
    break
  }
}
if (-not $py) {
  Write-Host "Python 3.11+ not found. Install from https://www.python.org/downloads/ (check Add to PATH), then re-run." -ForegroundColor Red
  exit 1
}

$script = if ($Cli) { "$Root\scripts\bootstrap_voxoryl.py" } else { "$Root\scripts\setup_voxoryl.py" }
if (-not $Cli) {
  Write-Host "Opening Setup Wizard (use -Cli for terminal-only bootstrap)…" -ForegroundColor Cyan
}

if ($py -eq "py") {
  & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Need Python 3.11+. Current launcher is too old." -ForegroundColor Red
    exit 1
  }
  & py -3 $script @args
} else {
  & python $script @args
}
exit $LASTEXITCODE
