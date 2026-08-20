<#
.SYNOPSIS
    One unattended refresh: fetch, rebuild, re-price, notify. Logged to logs\.

.DESCRIPTION
    This is what Task Scheduler calls. It exists so the scheduled task holds one
    stable command line, while what a run actually does stays in fb.py.

    Nothing here is interactive: no prompts, no window, and any failure ends up
    in the log with a non-zero exit code rather than on a screen nobody is
    watching.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_daily.ps1
    powershell -ExecutionPolicy Bypass -File scripts\run_daily.ps1 -VbArgs '--no-odds'
#>
param(
    # Interpreter to use. Defaults to whatever `python` resolves to, which is
    # the one that installed requirements.txt if you used a plain install.
    [string]$Python = "",

    # Extra arguments passed straight through to `fb.py run`.
    [string[]]$VbArgs = @(),

    # Keep this many days of logs. Older ones are deleted after each run.
    [int]$KeepLogDays = 30
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$log = Join-Path $logDir ("run-{0:yyyy-MM-dd}.log" -f (Get-Date))

if (-not $Python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) {
        "[{0:yyyy-MM-dd HH:mm:ss}] python not found on PATH" -f (Get-Date) |
            Add-Content -Path $log -Encoding utf8
        exit 1
    }
    $Python = $found.Source
}

# The pick message carries emoji. Without this the child process would die on
# the console codepage before it ever reached Telegram.
$env:PYTHONIOENCODING = "utf-8"

Set-Location $root
"" | Add-Content -Path $log -Encoding utf8
"===== {0:yyyy-MM-dd HH:mm:ss} : {1} =====" -f (Get-Date), $Python |
    Add-Content -Path $log -Encoding utf8

# Continue, not Stop, for the child process only. Windows PowerShell wraps a
# native command's stderr in an ErrorRecord, so under -ErrorAction Stop a single
# pandas warning would end the run before the pipeline had done anything.
#
# Two things about the pipeline below, both learned from a log nobody could read:
# an ErrorRecord renders as a five-line diagnostic blob unless it is reduced to
# its message first, and Tee-Object writes UTF-16, which turns every earlier
# UTF-8 line into spaced-out letters. Out-File with an explicit encoding fixes
# the second; the ForEach-Object fixes the first.
$ErrorActionPreference = "Continue"
# -u so the log fills in as the run progresses. Piped, Python block-buffers its
# stdout, and a log that only appears at the end is useless while you wait.
& $Python "-u" "fb.py" "run" @VbArgs 2>&1 |
    ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message }
        else { $_ }
    } | Out-File -FilePath $log -Append -Encoding utf8
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

"[{0:yyyy-MM-dd HH:mm:ss}] exit code {1}" -f (Get-Date), $code |
    Add-Content -Path $log -Encoding utf8

Get-ChildItem $logDir -Filter "run-*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepLogDays) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $code
