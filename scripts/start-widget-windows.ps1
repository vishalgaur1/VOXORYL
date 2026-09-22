#Requires -Version 5.1
<#
  Starts the product launcher (Ollama + hidden API + widget + clean stop on close).
  Prefer the Desktop Voxoryl icon after: scripts\install-desktop-shortcut.ps1
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $Root)) { $Root = "D:\VOXORYL" }
& powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\install-desktop-shortcut.ps1")
$vbs = Join-Path $Root "scripts\launch-voxoryl.vbs"
if (Test-Path $vbs) {
  Start-Process -FilePath "$env:SystemRoot\System32\wscript.exe" -ArgumentList "//nologo `"$vbs`""
} else {
  $py = Join-Path $Root ".venv\Scripts\pythonw.exe"
  if (-not (Test-Path $py)) { $py = Join-Path $Root ".venv\Scripts\python.exe" }
  Start-Process -FilePath $py -ArgumentList "`"$(Join-Path $Root 'scripts\launch_voxoryl.py')`"" -WorkingDirectory $Root -WindowStyle Hidden
}
