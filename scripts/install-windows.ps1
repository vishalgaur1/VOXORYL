#Requires -Version 5.1
<#
.SYNOPSIS
  Creates D:\VOXORYL, copies this project, installs Python deps, pulls qwen3.5:4b.
#>
$ErrorActionPreference = "Stop"
$Target = "D:\VOXORYL"
$Source = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path "D:\")) {
  Write-Host "D: drive not found. Create it or edit Target in install-windows.ps1" -ForegroundColor Red
  exit 1
}

Write-Host "==> Creating $Target"
New-Item -ItemType Directory -Force -Path $Target | Out-Null

if ((Resolve-Path $Source).Path.TrimEnd("\") -ieq (Join-Path $Target "").TrimEnd("\") -or
    ((Test-Path $Target) -and (Resolve-Path $Source).Path.TrimEnd("\") -ieq (Resolve-Path $Target).Path.TrimEnd("\"))) {
  Write-Host "==> Project already at $Target — installing in place"
  Set-Location $Target
} else {
  Write-Host "==> Copying Voxoryl files to $Target"
  New-Item -ItemType Directory -Force -Path $Target | Out-Null
  robocopy $Source $Target /E /XD .git data __pycache__ .venv agent-tools /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed with code $LASTEXITCODE" }
  Set-Location $Target
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host "Python not found. Install Python 3.11+ from https://www.python.org/downloads/ (check Add to PATH)." -ForegroundColor Red
  exit 1
}

Write-Host "==> Creating virtualenv"
python -m venv .venv
& "$Target\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$Target\.venv\Scripts\pip.exe" install -r requirements.txt

if (-not (Test-Path "$Target\.env")) {
  Copy-Item "$Target\.env.example" "$Target\.env"
}

Write-Host "==> Bootstrapping private data from setup/voxoryl.setup.json (never overwrites your data)"
& "$Target\.venv\Scripts\python.exe" -m voxoryl.bootstrap

Write-Host "==> Checking Ollama + installing default models for clones"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
  Write-Host "Ollama missing. Download from https://ollama.com/download and re-run this script." -ForegroundColor Yellow
} else {
  Write-Host "==> Pulling default bundle (qwen3.5:4b + nomic-embed-text) then hardware advice"
  & "$Target\.venv\Scripts\python.exe" -m voxoryl.models_setup --install
  Write-Host ""
  Write-Host "==> Hardware model recommendation (upgrade/downgrade for THIS PC)" -ForegroundColor Cyan
  & "$Target\.venv\Scripts\python.exe" -m voxoryl.models_setup --recommend
  Write-Host ""
  Write-Host "To apply the suggested models into .env and pull them:" -ForegroundColor Yellow
  Write-Host "  .\.venv\Scripts\python.exe -m voxoryl.models_setup --apply"
}

# Desktop product shortcut — single Voxoryl.lnk only (never "Voxoryl (start)")
Write-Host "==> Creating Desktop Voxoryl shortcut (silent product launcher)"
& "$Target\.venv\Scripts\python.exe" "$Target\scripts\launch_voxoryl.py" --shortcut

Write-Host ""
Write-Host "Done. Product launch (no console):" -ForegroundColor Green
Write-Host "  Double-click Desktop 'Voxoryl'  or  wscript D:\VOXORYL\scripts\launch-voxoryl.vbs"
Write-Host "  .\.venv\Scripts\python.exe .\scripts\launch_voxoryl.py"
Write-Host "  Web dashboard from orb: 'Web dashboard' button, or --dashboard"
Write-Host "Developer console:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File D:\VOXORYL\scripts\start-windows.ps1 -Console"
Write-Host "  .\.venv\Scripts\python.exe .\run.py"
Write-Host "Close the widget to stop the Voxoryl API (Ollama left running if it was already up)."
Write-Host "Logs: D:\VOXORYL\data\logs\"
Write-Host "Dashboard: http://127.0.0.1:3848 (or :3847) — from widget or --dashboard"
Write-Host "Model advice API: http://127.0.0.1:3848/api/models"
