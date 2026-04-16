# 07 — 部署拓撲（Windows 本機單機）

> 對齊 OPERATIONS_RUNBOOK.md、EDD §8.1。

---

## 7.1 部署架構圖

```mermaid
flowchart TD
    subgraph Windows["Windows 本機 (AIBALA)"]
        subgraph TaskScheduler["Windows Task Scheduler"]
            TS_START["StockMonitor-Start\n⏰ Mon–Fri 08:50\n→ start_daemon.ps1"]
            TS_STOP["StockMonitor-Stop\n⏰ Mon–Fri 14:30\n→ stop_daemon.ps1"]
        end

        subgraph PS1["PowerShell Scripts (scripts/)"]
            direction TB
            START_PS1["start_daemon.ps1\nStart-Process -WindowStyle Hidden\n-RedirectStdOut logs/daemon.log\n-RedirectStdErr logs/daemon_err.log"]
            STOP_PS1["stop_daemon.ps1\nGet-WmiObject Win32_Process\nfilter CommandLine *stock_monitor*run-daemon*\nStop-Process -Force"]
        end

        subgraph Process["Python Process (Hidden)"]
            DAEMON["python -m stock_monitor\n  --db-path data/stock_monitor.db\n  run-daemon\n  --poll-interval-sec 60\n  --valuation-time 14:00"]
        end

        subgraph Storage["本機儲存"]
            direction TB
            DB["data/stock_monitor.db\nSQLite WAL mode"]
            LOG_D["logs/daemon.log"]
            LOG_E["logs/daemon_err.log"]
            JSONL["logs/pending_delivery.jsonl\n（補償 fallback）"]
            DB ~~~ LOG_D ~~~ LOG_E ~~~ JSONL
        end

        subgraph Env["環境變數（setx 持久化）"]
            direction TB
            ENV_TOKEN["LINE_CHANNEL_ACCESS_TOKEN"]
            ENV_GROUP["LINE_TO_GROUP_ID"]
            ENV_TOKEN ~~~ ENV_GROUP
        end
    end

    subgraph External["外部服務"]
        direction TB
        TWSE_EXT["TWSE MIS API"]
        YAHOO_EXT["Yahoo Finance TW HTML"]
        LINE_EXT["LINE Messaging API"]
        TWSE_EXT ~~~ YAHOO_EXT ~~~ LINE_EXT
    end

    TS_START -- "08:50 觸發" --> START_PS1
    TS_STOP  -- "14:30 觸發" --> STOP_PS1
    START_PS1 -- "Start-Process" --> DAEMON
    STOP_PS1  -- "Stop-Process" --> DAEMON

    DAEMON -- "每 60 秒輪詢" --> TWSE_EXT
    DAEMON -- "備援行情" --> YAHOO_EXT
    DAEMON -- "發通知" --> LINE_EXT
    DAEMON -- "讀寫" --> DB
    DAEMON -- "stdout" --> LOG_D
    DAEMON -- "stderr" --> LOG_E
    DAEMON -- "補償 fallback" --> JSONL
    DAEMON -- "讀取" --> ENV_TOKEN & ENV_GROUP
```

---

## 7.2 排程工作清單

| 工作名稱 | 觸發時間 | 執行腳本 | 備註 |
|---|---|---|---|
| `StockMonitor-Start` | 週一至週五 08:50 | `scripts/start_daemon.ps1` | `Start-Process -WindowStyle Hidden`，PID 寫入 Event Log |
| `StockMonitor-Stop` | 週一至週五 14:30 | `scripts/stop_daemon.ps1` | 以 CommandLine 識別 PID，`Stop-Process -Force` |

---

## 7.3 Process 生命週期

```mermaid
stateDiagram-v2
    [*] --> Idle : 系統開機 / 非交易時段

    Idle --> Starting : 08:50 Task Scheduler 觸發

    Starting --> HealthCheck : bootstrap 檢查\n(SQLite JSON1 / foreign_keys / LINE token)
    HealthCheck --> Failed : fail-fast\n(token 缺失 / DB 異常)
    Failed --> [*]

    HealthCheck --> Trading : 09:00 進入交易時段
    Trading --> Trading : 每 60 秒：fetch → evaluate → notify → persist
    Trading --> Valuation : 13:30 交易結束
    Valuation --> Valuation : 等待 14:00
    Valuation --> ValuationJob : 14:00 觸發日結
    ValuationJob --> PostMarket : 估值完成 / skip
    PostMarket --> Stopping : 14:30 Task Scheduler 觸發 Stop

    Stopping --> [*]
```

---

## 7.4 Log 檔位置

| 檔案 | 內容 |
|---|---|
| `logs/daemon.log` | daemon 標準輸出（INFO / WARN 事件） |
| `logs/daemon_err.log` | daemon 標準錯誤（Python exception traceback） |
| `logs/pending_delivery.jsonl` | DB 不可寫時的補償記錄（JSONL fallback） |
| SQLite `system_logs` 表 | 結構化事件記錄（可 SQL 查詢） |

---

## 7.5 快速維運指令

```powershell
# 查詢排程狀態與下次執行時間
schtasks /Query /TN "StockMonitor-Start" /FO LIST
schtasks /Query /TN "StockMonitor-Stop" /FO LIST

# 手動立即啟動
schtasks /Run /TN "StockMonitor-Start"

# 手動立即停止
schtasks /Run /TN "StockMonitor-Stop"

# 查看最近 system_logs（需進入 Python）
python -c "
import sqlite3
conn = sqlite3.connect('data/stock_monitor.db')
rows = conn.execute('''
  SELECT datetime(created_at, 'unixepoch', '+8 hours') AS ts, level, event, detail
  FROM system_logs ORDER BY id DESC LIMIT 20
''').fetchall()
[print(r) for r in rows]
conn.close()
"

# 手動執行估值（非 14:00 也可強制）
python -m stock_monitor --db-path data/stock_monitor.db valuation-once
```
