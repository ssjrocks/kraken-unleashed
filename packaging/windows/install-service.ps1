<#
    Register Kraken Unleashed to run at boot on Windows.

    Run from an ADMINISTRATOR PowerShell, in the folder you unzipped:
        .\install-service.ps1

    This creates a Scheduled Task that starts the daemon at boot with the
    highest privileges. A Scheduled Task is used rather than a true Windows
    service because the daemon is a plain console program: wrapping it in a
    service would need an extra service-host dependency for no practical gain.

    Remove it again with .\uninstall-service.ps1
#>
#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'
$TaskName = 'KrakenUnleashed'
$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Exe      = Join-Path $Here 'kraken-unleashed-daemon.exe'

if (-not (Test-Path $Exe)) {
    Write-Error "kraken-unleashed-daemon.exe not found next to this script ($Here)."
}

Write-Host '== Kraken Unleashed ==' -ForegroundColor Cyan

# Check the device and drivers before registering anything, so a driver problem
# surfaces now rather than as a task that silently fails at every boot.
Write-Host 'Checking the device...'
& (Join-Path $Here 'kraken-unleashed-ctl.exe') diagnose
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'The device check did not pass. See docs/WINDOWS.md -- you most'
    Write-Warning 'likely need to bind WinUSB to INTERFACE 0 only, using Zadig.'
    $answer = Read-Host 'Register the task anyway? [y/N]'
    if ($answer -ne 'y') { exit 1 }
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Removing the existing $TaskName task"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action    = New-ScheduledTaskAction -Execute $Exe -WorkingDirectory $Here
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount `
                                        -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -StartWhenAvailable `
                -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'Kraken Unleashed: LCD and lighting for the NZXT Kraken 2024' | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4

$state = (Get-ScheduledTask -TaskName $TaskName).State
Write-Host "Task '$TaskName' registered and started (state: $state)" -ForegroundColor Green
Write-Host ''
Write-Host 'Check it with:'
Write-Host '    .\kraken-unleashed-ctl.exe status'
Write-Host 'Change settings in:'
Write-Host '    C:\ProgramData\KrakenUnleashed\config.json'
Write-Host 'then restart the task:'
Write-Host "    Restart-ScheduledTask -TaskName $TaskName"
