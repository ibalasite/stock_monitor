# register_scheduled_tasks.ps1
# Registers two Windows Scheduled Tasks for StockMonitor:
#   StockMonitor-Start  — weekdays 08:50 (start daemon)
#   StockMonitor-Stop   — weekdays 14:30 (stop daemon)
# Run once as Administrator via:
#   powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_tasks.ps1

# Register custom event source (safe to ignore if already exists)
New-EventLog -LogName Application -Source 'StockMonitor' -ErrorAction SilentlyContinue

# ---- Action objects ----
$startAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -File C:\Projects\stock\scripts\start_daemon.ps1" `
    -WorkingDirectory "C:\Projects\stock"

$stopAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -File C:\Projects\stock\scripts\stop_daemon.ps1"

# ---- Trigger objects ----
$startTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "08:50"

$stopTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "14:30"

# ---- Shared settings ----
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

# ---- Register tasks ----
Register-ScheduledTask `
    -TaskName "StockMonitor-Start" `
    -Action $startAction `
    -Trigger $startTrigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "Start stock monitor daemon at 08:50 on weekdays" `
    -Force

Write-Host "StockMonitor-Start registered."

Register-ScheduledTask `
    -TaskName "StockMonitor-Stop" `
    -Action $stopAction `
    -Trigger $stopTrigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "Stop stock monitor daemon at 14:30 on weekdays" `
    -Force

Write-Host "StockMonitor-Stop registered."

# ---- Verify ----
Write-Host ""
Write-Host "=== Registered tasks ==="
$tasks = @("StockMonitor-Start", "StockMonitor-Stop")
foreach ($name in $tasks) {
    $t  = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    $ti = Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue
    if ($t) {
        Write-Host ("  {0}  State={1}  NextRun={2}" -f $name, $t.State, $ti.NextRunTime)
    }
}
