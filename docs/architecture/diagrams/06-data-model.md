# 06 — 資料模型（SQLite ER 圖）

> 對齊 EDD §6。

---

## 6.1 ER 圖

```mermaid
erDiagram
    watchlist {
        TEXT    stock_no           PK
        NUMERIC manual_fair_price  "constraint: cheap le fair"
        NUMERIC manual_cheap_price
        INTEGER enabled            "0 or 1"
        INTEGER created_at         "epoch sec UTC"
        INTEGER updated_at         "epoch sec UTC"
    }

    valuation_methods {
        TEXT    method_name    PK
        TEXT    method_version PK
        INTEGER enabled        "0 or 1"
        INTEGER created_at
        INTEGER updated_at
    }

    valuation_snapshots {
        INTEGER id             PK
        TEXT    stock_no       FK
        TEXT    trade_date     "YYYY-MM-DD"
        TEXT    method_name    FK
        TEXT    method_version FK
        NUMERIC fair_price     "constraint: cheap le fair"
        NUMERIC cheap_price
        INTEGER created_at
    }

    message {
        INTEGER id             PK
        TEXT    stock_no       FK
        TEXT    message
        INTEGER stock_status   "1 or 2"
        TEXT    methods_hit    "JSON array"
        TEXT    minute_bucket  "YYYY-MM-DD HH:mm"
        INTEGER update_time    "epoch sec UTC"
    }

    pending_delivery_ledger {
        INTEGER id             PK
        TEXT    minute_bucket
        TEXT    payload_json   "json_valid"
        TEXT    status         "PENDING RECONCILED FAILED"
        INTEGER retry_count
        TEXT    last_error
        INTEGER created_at
        INTEGER updated_at
    }

    system_logs {
        INTEGER id         PK
        TEXT    level      "INFO WARN ERROR"
        TEXT    event
        TEXT    detail
        INTEGER created_at "epoch sec UTC"
    }

    opening_summary_sent_dates {
        TEXT    trade_date PK "YYYY-MM-DD"
        INTEGER sent_at    "epoch sec UTC"
    }

    watchlist         ||--o{ valuation_snapshots : "stock_no"
    watchlist         ||--o{ message             : "stock_no"
    valuation_methods ||--o{ valuation_snapshots : "method_name + version"
```

---

## 6.2 關鍵索引與約束說明

| 表 | 關鍵約束 | 用途 |
|---|---|---|
| `watchlist` | `CHECK (manual_cheap_price <= manual_fair_price)` | 防止非法設定 |
| `valuation_methods` | `UNIQUE INDEX(method_name) WHERE enabled=1` | 同方法名只允許一個 enabled=1 |
| `valuation_snapshots` | `UNIQUE(stock_no, trade_date, method_name, method_version)` | 日結防重複；upsert 冪等 |
| `message` | `UNIQUE(stock_no, minute_bucket)` | 同分鐘冪等保護 |
| `message` | `INDEX(stock_no, stock_status, update_time DESC)` | 冷卻查詢效率 |
| `message.methods_hit` | `json_valid() AND json_type()='array'` | 強制 JSON array 格式 |
| `pending_delivery_ledger.status` | `CHECK IN('PENDING','RECONCILED','FAILED')` | 補償狀態合法性 |

---

## 6.3 補償流程資料流

```mermaid
sequenceDiagram
    participant App as monitoring_workflow
    participant LINE as LINE Messaging API
    participant DB as SQLite (message)
    participant PL as pending_delivery_ledger
    participant JSONL as logs/pending_delivery.jsonl

    App->>LINE: push_message(minute_digest)
    LINE-->>App: HTTP 200 OK

    App->>DB: BEGIN TRANSACTION
    App->>DB: INSERT INTO message ... (all stock events)
    DB-->>App: ROLLBACK (DB error)

    App->>PL: INSERT INTO pending_delivery_ledger (PENDING)
    alt DB 也不可寫
        App->>JSONL: append JSON line (fallback)
    end
    Note over App: 補償期間視同已通知（不重發 LINE）

    loop reconcile_pending_once（每圈）
        App->>PL: SELECT * WHERE status='PENDING'
        App->>DB: INSERT INTO message (reconcile)
        DB-->>App: OK
        App->>PL: UPDATE status='RECONCILED'
    end
```
