# 06 — 資料模型（SQLite ER 圖）

> 對齊 EDD §6。

---

## 6.1 ER 圖

```mermaid
flowchart TD
    WL["watchlist\n────────────\nstock_no PK\nmanual_fair_price  ⟨cheap ≤ fair⟩\nmanual_cheap_price\nenabled  0/1\ncreated_at  epoch UTC\nupdated_at  epoch UTC"]

    VM["valuation_methods\n────────────\nmethod_name PK\nmethod_version PK\nenabled  0/1\ncreated_at\nupdated_at"]

    VS["valuation_snapshots\n────────────\nid PK\nstock_no FK\ntrade_date  YYYY-MM-DD\nmethod_name FK\nmethod_version FK\nfair_price  ⟨cheap ≤ fair⟩\ncheap_price\ncreated_at"]

    MSG["message\n────────────\nid PK\nstock_no FK\nmessage\nstock_status  1 or 2\nmethods_hit  JSON array\nminute_bucket  YYYY-MM-DD HH:mm\nupdate_time  epoch UTC"]

    PDL["pending_delivery_ledger\n────────────\nid PK\nminute_bucket\npayload_json\nstatus  PENDING/RECONCILED/FAILED\nretry_count\nlast_error\ncreated_at\nupdated_at"]

    SL["system_logs\n────────────\nid PK\nlevel  INFO/WARN/ERROR\nevent\ndetail\ncreated_at  epoch UTC"]

    OSD["opening_summary_sent_dates\n────────────\ntrade_date PK  YYYY-MM-DD\nsent_at  epoch UTC"]

    WL -- "stock_no" --> VS
    WL -- "stock_no" --> MSG
    VM -- "method_name+version" --> VS
    VS ~~~ PDL
    MSG ~~~ SL
    PDL ~~~ OSD
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
