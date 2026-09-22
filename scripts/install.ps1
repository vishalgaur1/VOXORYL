#Requires -Version 5.1
<#
.SYNOPSIS
  Zero-setup VOXORYL install (Windows).

.DESCRIPTION
  Thin wrapper around scripts/bootstrap_voxoryl.py — creates venv, deps,
  OS user-data under %LOCALAPPDATA%\VOXORYL, picks/pulls a fitting Ollama
  model, then launches the voice widget.
#>
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

# Prefer `py -3.12` / `py -3.11` when available
if ($py -eq "py") {
  & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Need Python 3.11+. Current launcher is too old." -ForegroundColor Red
    exit 1
  }
  & py -3 "$Root\scripts\bootstrap_voxoryl.py" @args
} else {
  & python "$Root\scripts\bootstrap_voxoryl.py" @args
}
exit $LASTEXITCODE
