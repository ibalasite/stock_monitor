# 04 — 每日 14:00 估值日結流程

> 對齊 EDD §4.2、§9.1、§9.2。

---

## 4.1 估值日結 Flowchart

```mermaid
flowchart TD
    TRIG["⏰ 14:00 觸發\n（daemon loop 偵測 now_hhmm == '14:00'）"]
    WEEKDAY{今日是\n工作日？}
    SKIP_WD["Skip（假日/週末）"]
    DEDUP{今日已執行\n估值日結？}
    SKIP_DUP["Skip（已執行過，防重複）"]

    WATCH["載入 enabled watchlist\n（stock_no 列表）"]
    METHODS["載入 enabled valuation_methods\n（emily / oldbull / raysky / manual_rule）"]
    INPUT["載入各方法所需輸入資料\n（price_history / avg_dividend / EPS / BPS / ...）"]

    LOOP_S["for each stock × method"]

    EMILY["emily_composite_v1\nfair = avg(股利 / 歷年股價 / PE / PB 子法) × 1.0\ncheap = avg × 0.9"]
    OLDBULL["oldbull_dividend_yield_v1\nfair = avg_dividend / 0.05\ncheap = avg_dividend / 0.06"]
    RAYSKY["raysky_blended_margin_v1\nfair = median(PE / 股利 / PB / NCAV 子法)\ncheap = fair × margin_factor(0.9)"]
    MANUAL["manual_rule\nfair = watchlist.manual_fair_price\ncheap = watchlist.manual_cheap_price"]

    STATUS{計算狀態}
    SUCCESS["SUCCESS\nfair_price / cheap_price 有效"]
    SKIP_DATA["SKIP_INSUFFICIENT_DATA\n缺 required_fields"]
    SKIP_ERR["SKIP_PROVIDER_ERROR\n資料來源回傳空值或逾時"]

    UPSERT["INSERT OR REPLACE INTO valuation_snapshots\n(stock_no, trade_date, method_name, method_version,\n fair_price, cheap_price, created_at)\nUNIQUE(stock_no, trade_date, method_name, method_version)"]
    KEEP["保留既有快照\n不覆蓋"]
    WARN_LOG["寫 system_logs WARN/ERROR"]

    LOOP_E["下一個 stock × method"]
    DONE["🏁 估值日結完成\n寫 system_logs VALUATION_EXECUTED: N"]

    TRIG --> WEEKDAY
    WEEKDAY -- No --> SKIP_WD
    WEEKDAY -- Yes --> DEDUP
    DEDUP -- Yes --> SKIP_DUP
    DEDUP -- No --> WATCH --> METHODS --> INPUT --> LOOP_S
    LOOP_S --> EMILY & OLDBULL & RAYSKY & MANUAL
    EMILY & OLDBULL & RAYSKY & MANUAL --> STATUS
    STATUS -- SUCCESS --> SUCCESS --> UPSERT --> LOOP_E
    STATUS -- SKIP_INSUFFICIENT_DATA --> SKIP_DATA --> KEEP --> WARN_LOG --> LOOP_E
    STATUS -- SKIP_PROVIDER_ERROR --> SKIP_ERR --> KEEP --> WARN_LOG
    LOOP_E --> DONE
```

---

## 4.2 三方法公式速查

| 方法 | fair_price 公式 | cheap_price 公式 |
|---|---|---|
| `emily_composite_v1` | `avg(股利法/歷年股價法/PE法/PB法 fair) × 安全邊際` | `fair × 0.9` |
| `oldbull_dividend_yield_v1` | `avg_dividend ÷ 0.05` | `avg_dividend ÷ 0.06` |
| `raysky_blended_margin_v1` | `median(PE/股利/PB/NCAV 子法 fair)` | `fair × margin_factor(0.9)` |
| `manual_rule` | `watchlist.manual_fair_price` | `watchlist.manual_cheap_price` |

---

## 4.3 估值狀態說明

| 狀態 | 意義 | 對快照的影響 |
|---|---|---|
| `SUCCESS` | 計算完成，資料充足 | 寫入（upsert）`valuation_snapshots` |
| `SKIP_INSUFFICIENT_DATA` | required fields 缺失 | **不覆蓋** 既有快照，寫 WARN log |
| `SKIP_PROVIDER_ERROR` | 資料來源失敗 | **不覆蓋** 既有快照，寫 ERROR log |

> 日結成功條件：至少一個 `stock × method` 為 `SUCCESS` 即視為 job 完成（允許部分 skip）。
