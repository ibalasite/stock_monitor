# Stock Monitor Daemon - Start Script
# Triggered by Windows Task Scheduler on weekdays at 08:50
#
# CR-SEC-07: Credentials are stored in Windows Credential Manager.
# One-time setup (run once as the same user that runs this script):
#   cmdkey /add:stock_monitor_LINE_TOKEN    /user:LINE_TOKEN    /pass:"<your_token>"
#   cmdkey /add:stock_monitor_LINE_GROUP_ID /user:LINE_GROUP_ID /pass:"<your_group_id>"

# Retrieve credentials from Windows Credential Manager at runtime (CR-SEC-07).
# Requires: Install-Module -Name CredentialManager -Scope CurrentUser  (one-time)
$env:LINE_CHANNEL_ACCESS_TOKEN = (Get-StoredCredential -Target stock_monitor_LINE_TOKEN).Password
$env:LINE_TO_GROUP_ID          = (Get-StoredCredential -Target stock_monitor_LINE_GROUP_ID).Password

$python  = "C:\Users\ibala\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"
$workDir = "C:\Projects\stock"
$logFile = "C:\Projects\stock\logs\daemon.log"

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path "$workDir\logs" | Out-Null

# Start daemon as background job (non-blocking), redirect stdout+stderr to log
$proc = Start-Process `
    -FilePath $python `
    -ArgumentList "-m", "stock_monitor", "--db-path", "data/stock_monitor.db", "run-daemon", "--poll-interval-sec", "60", "--valuation-time", "14:00" `
    -WorkingDirectory $workDir `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError "$workDir\logs\daemon_err.log" `
    -WindowStyle Hidden `
    -PassThru

Write-EventLog -LogName Application -Source "StockMonitor" -EventId 1001 -EntryType Information `
    -Message "Stock Monitor daemon started. PID=$($proc.Id)" -ErrorAction SilentlyContinue
Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') daemon started PID=$($proc.Id)"
