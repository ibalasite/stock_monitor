# 06 — 資料模型（SQLite ER 圖）

> 對齊 EDD §6。

---

## 6.1 ER 圖

```mermaid
%%{init: {"er": {"layoutDirection": "TB"}} }%%
erDiagram
    watchlist {
        TEXT stock_no PK
        REAL manual_fair_price "cheap≤fair"
        REAL manual_cheap_price
        INT  enabled "0/1"
        INT  created_at "epoch UTC"
        INT  updated_at "epoch UTC"
    }
    valuation_methods {
        TEXT method_name PK
        TEXT method_version PK
        INT  enabled "0/1"
        INT  created_at
        INT  updated_at
    }
    valuation_snapshots {
        INT  id PK
        TEXT stock_no FK
        TEXT trade_date "YYYY-MM-DD"
        TEXT method_name FK
        TEXT method_version FK
        REAL fair_price "cheap≤fair"
        REAL cheap_price
        INT  created_at
    }
    message {
        INT  id PK
        TEXT stock_no FK
        TEXT message
        INT  stock_status "1 or 2"
        JSON methods_hit "array"
        TEXT minute_bucket "YYYY-MM-DD HH:mm"
        INT  update_time "epoch UTC"
    }
    pending_delivery_ledger {
        INT  id PK
        TEXT minute_bucket
        JSON payload_json
        TEXT status "PENDING/RECONCILED/FAILED"
        INT  retry_count
        TEXT last_error
        INT  created_at
        INT  updated_at
    }
    system_logs {
        INT  id PK
        TEXT level "INFO/WARN/ERROR"
        TEXT event
        TEXT detail
        INT  created_at "epoch UTC"
    }
    opening_summary_sent_dates {
        TEXT trade_date PK "YYYY-MM-DD"
        INT  sent_at "epoch UTC"
    }

    watchlist ||--o{ valuation_snapshots : "stock_no"
    watchlist ||--o{ message : "stock_no"
    valuation_methods ||--o{ valuation_snapshots : "method_name+version"
    message ||--o{ pending_delivery_ledger : "補償"
    message ||--o{ system_logs : "事件記錄"
    valuation_snapshots ||--o{ opening_summary_sent_dates : "日結"
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
