#Requires -Version 5.1
<#
  Product start path used by Desktop / VBS fallback.
  Starts Ollama (if needed), Voxoryl API (silent), and the voice widget.
  Logs: D:\VOXORYL\data\logs\
#>
param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$LaunchArgs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Py)) {
  $Py = Join-Path $Root ".venv\Scripts\python.exe"
}
if (-not (Test-Path $Py)) {
  throw "Virtualenv missing. Run scripts\install-windows.ps1 first."
}
$launch = Join-Path $Root "scripts\launch_voxoryl.py"
$allArgs = @($launch) + @($LaunchArgs)
Start-Process -FilePath $Py -ArgumentList $allArgs -WorkingDirectory $Root -WindowStyle Hidden
