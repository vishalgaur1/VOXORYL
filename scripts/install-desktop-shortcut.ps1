#Requires -Version 5.1
<#
  Creates/updates the single Desktop "Voxoryl" shortcut (never "Voxoryl (start)").
  Also removes duplicate Voxoryl*.lnk from Desktop + OneDrive Desktop.
  Equivalent:  .\.venv\Scripts\python.exe .\scripts\launch_voxoryl.py --shortcut
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $Root)) { $Root = "D:\VOXORYL" }
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "Virtualenv missing. Run scripts\install-windows.ps1 first." }
& $Py (Join-Path $Root "scripts\launch_voxoryl.py") --shortcut
Write-Host "Desktop icon: Voxoryl.lnk only (duplicates purged)." -ForegroundColor Green
Write-Host "Logs: $Root\data\logs\server.log" -ForegroundColor Green
