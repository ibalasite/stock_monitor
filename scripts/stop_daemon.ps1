# Stock Monitor Daemon - Stop Script
# Triggered by Windows Task Scheduler on weekdays at 14:30

$stopped = @()
Get-WmiObject Win32_Process -Filter "Name LIKE 'python%'" | ForEach-Object {
    if ($_.CommandLine -like "*stock_monitor*run-daemon*") {
        $pid = $_.ProcessId
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        $stopped += $pid
    }
}

if ($stopped.Count -gt 0) {
    $msg = "Stock Monitor daemon stopped. PIDs=$($stopped -join ',')"
} else {
    $msg = "Stock Monitor stop task ran but no daemon process found."
}

Write-EventLog -LogName Application -Source "StockMonitor" -EventId 1002 -EntryType Information `
    -Message $msg -ErrorAction SilentlyContinue
Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
