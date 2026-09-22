#Requires -Version 5.1
<#
  Start Voxoryl API (and Ollama if needed).

  Default: silent / product mode — no console, logs under data\logs\
  Developer:  .\scripts\start-windows.ps1 -Console
             or:  python run.py
#>
param(
  [switch]$Console
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
  Write-Host "Virtualenv missing. Run scripts\install-windows.ps1 first." -ForegroundColor Red
  exit 1
}

if (-not (Test-Path "$Root\.env")) {
  Copy-Item "$Root\.env.example" "$Root\.env"
}

$env:VOXORYL_ALLOW_MOCK = "0"
$Launch = Join-Path $Root "scripts\launch_voxoryl.py"

if ($Console) {
  & "$Root\.venv\Scripts\python.exe" $Launch --console --server-only
} else {
  $exe = if (Test-Path "$Root\.venv\Scripts\pythonw.exe") {
    "$Root\.venv\Scripts\pythonw.exe"
  } else {
    "$Root\.venv\Scripts\python.exe"
  }
  # Detached silent server (no widget)
  Start-Process -FilePath $exe -ArgumentList @($Launch, "--no-widget") -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
  Write-Host "Voxoryl starting quietly. Logs: $Root\data\logs\" -ForegroundColor Green
  Write-Host "Widget: powershell -File $Root\scripts\start-widget-windows.ps1" -ForegroundColor DarkGray
  Write-Host "Or double-click Desktop Voxoryl / scripts\launch-voxoryl.vbs" -ForegroundColor DarkGray
}
