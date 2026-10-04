<#
    Remove the Kraken Unleashed scheduled task.
    Run from an ADMINISTRATOR PowerShell:  .\uninstall-service.ps1
#>
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$TaskName = 'KrakenUnleashed'

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask  -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed the $TaskName task" -ForegroundColor Green
} else {
    Write-Host "No $TaskName task registered"
}
Write-Host 'Your settings in C:\ProgramData\KrakenUnleashed were left alone.'
Write-Host 'The cooler keeps the last frame it was sent until something else'
Write-Host 'writes to it; its own screen returns after a power cycle.'
