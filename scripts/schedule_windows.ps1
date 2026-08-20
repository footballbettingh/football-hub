<#
.SYNOPSIS
    Register (or remove) the daily Football Hub run in Windows Task Scheduler.

.DESCRIPTION
    Registers one task that calls scripts\run_daily.ps1 every day. No admin
    rights needed: the task runs as you, when you are logged on.

    -StartWhenAvailable is the important setting. A laptop that was asleep at
    the scheduled minute runs the job as soon as it wakes, instead of silently
    skipping the day.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1
    powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Time 08:00
    powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -RunNow
    powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Remove
#>
param(
    # Local time, 24h. 09:15 is late enough for yesterday's European fixtures
    # to have settled and football-data.co.uk to have published them.
    [string]$Time = "09:15",

    [string]$TaskName = "FootballHub Daily",

    # Passed through to fb.py run, e.g. '--only-if-changed' or '--no-odds'.
    [string]$VbArgs = "",

    # Delete the task instead of creating it.
    [switch]$Remove,

    # Start the task once immediately after registering, to prove it works.
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root "scripts\run_daily.ps1"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($Remove) {
    if (-not $existing) {
        Write-Host "No task named '$TaskName' - nothing to remove."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
    exit 0
}

if (-not (Test-Path $runner)) {
    throw "Runner not found at $runner"
}

# -Command, not -File. Under -File the arguments are handed over as literal
# text, so a quoted '--only-if-changed' reached Python with its quotes still
# attached and argparse rejected it. -Command hands PowerShell a line of
# PowerShell to parse, where quoting means what it looks like it means.
$inner = "& '$runner'"
if ($VbArgs) {
    $quoted = ($VbArgs -split '\s+' | Where-Object { $_ } |
               ForEach-Object { "'$_'" }) -join ','
    $inner += " -VbArgs $quoted"
}
$argline = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$inner`""

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argline -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Refresh data, rebuild the model, re-price the card, send the best pick to Telegram." `
    -Force | Out-Null

Write-Host "Registered '$TaskName' - daily at $Time, running as $env:USERNAME."
Write-Host "  runner : $runner"
Write-Host "  logs   : $(Join-Path $root 'logs')"
Write-Host ""
Write-Host "Check it:  Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "Remove it: powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Remove"

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host ""
    Write-Host "Started once now. Watch it: Get-Content -Wait (Join-Path '$root' 'logs\run-$(Get-Date -Format yyyy-MM-dd).log')"
}
