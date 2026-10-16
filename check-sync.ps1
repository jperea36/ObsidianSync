<#
.SYNOPSIS
    Shows ObsidianSync task status and recent log output.

.EXAMPLE
    .\check-sync.ps1              # status + last 30 log lines
    .\check-sync.ps1 -Lines 100   # status + last 100 lines
    .\check-sync.ps1 -Problems    # only warnings and errors
    .\check-sync.ps1 -Follow      # live view (Ctrl+C to exit)
#>
param(
    [int]$Lines = 30,
    [switch]$Problems,
    [switch]$Follow
)

$TaskName     = "ObsidianSync"
$LogFile      = Join-Path $PSScriptRoot "sync.log"
$StartupError = Join-Path $PSScriptRoot "startup_error.log"

function Show-Line([string]$line) {
    if     ($line -match "\[ERROR\]|\[CRITICAL\]") { Write-Host $line -ForegroundColor Red }
    elseif ($line -match "\[WARNING\]")            { Write-Host $line -ForegroundColor Yellow }
    else                                           { Write-Host $line }
}

# --- Task status ---
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $info  = Get-ScheduledTaskInfo -TaskName $TaskName
    $color = if ($task.State -eq "Running") { "Green" } else { "Yellow" }
    Write-Host "Task:     $TaskName"
    Write-Host "State:    $($task.State)" -ForegroundColor $color
    Write-Host "Last run: $($info.LastRunTime)"
} else {
    Write-Host "Task '$TaskName' not found." -ForegroundColor Red
}

if (Test-Path $StartupError) {
    Write-Host "`nstartup_error.log found (script failed before logging started):" -ForegroundColor Red
    Get-Content $StartupError
}

if (-not (Test-Path $LogFile)) {
    Write-Host "`nNo log file at $LogFile" -ForegroundColor Red
    return
}

# --- Summary ---
$lastReconcile = Select-String -Path $LogFile -Pattern "Reconcile done" | Select-Object -Last 1
$problemCount  = (Select-String -Path $LogFile -Pattern "\[WARNING\]|\[ERROR\]|\[CRITICAL\]").Count
Write-Host "Last reconcile: $(if ($lastReconcile) { $lastReconcile.Line } else { 'none yet' })"
Write-Host "Warnings/errors in log: $problemCount" -ForegroundColor $(if ($problemCount) { "Yellow" } else { "Green" })
Write-Host ""

# --- Log output ---
if ($Problems) {
    Select-String -Path $LogFile -Pattern "\[WARNING\]|\[ERROR\]|\[CRITICAL\]" |
        ForEach-Object { Show-Line $_.Line }
} elseif ($Follow) {
    Get-Content $LogFile -Tail $Lines -Wait | ForEach-Object { Show-Line $_ }
} else {
    Get-Content $LogFile -Tail $Lines | ForEach-Object { Show-Line $_ }
}
