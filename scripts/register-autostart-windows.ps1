#Requires -Version 5.1
<#
  Registers a Windows logon task so Voxoryl starts 24x7 after reboot (silent).
#>
$ErrorActionPreference = "Stop"
$Root = "D:\VOXORYL"
$Vbs = Join-Path $Root "scripts\launch-voxoryl.vbs"
if (-not (Test-Path $Vbs)) {
  throw "Install Voxoryl to D:\VOXORYL first."
}

# Prefer VBS (no PowerShell flash). Product script starts Ollama + API + widget.
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$Vbs`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "VoxorylLocal" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered scheduled task 'VoxorylLocal' — silent Voxoryl start at logon." -ForegroundColor Green
Write-Host "Entry: $Vbs" -ForegroundColor DarkGray
