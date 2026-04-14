# 03 — 盤中每分鐘監控主流程

> 對齊 EDD §4.1、§2.1–§2.6。

---

## 3.1 主流程 Flowchart

```mermaid
flowchart TD
    TICK["⏱ Tick（每 60 秒）"]
    SESS{是交易時段？\n09:00–13:30}
    SKIP_SESS["跳過（非交易時段）"]

    MKTCHECK{大盤資料\n今日有新資料？}
    MKTFAIL["MARKET_TIMEOUT / DATA_CONFLICT\n寫 system_logs\n**本分鐘不補發**"]

    OPENCHK{本日開盤摘要\n已發送？}
    OPENSEND["推送開盤監控設定摘要\n（含全股票 × 全方法 fair/cheap）\n寫 opening_summary_sent_dates DB"]

    WATCH["載入 enabled watchlist"]
    FETCH["fetch_market_with_retry\n（最多 MAX_RETRY_COUNT=3 次）"]
    STALE{行情 tick_at 是否過期？\nSTALE_THRESHOLD_SEC=90}
    STALE_SKIP["STALE_QUOTE\n寫 WARN log\n**本分鐘不補發**"]
    RETRY_EX["重試耗盡\n寫 system_logs\n**本分鐘不補發**"]

    EVAL["對每個 stock × enabled method 計算訊號\n（manual_rule / emily / oldbull / raysky）"]
    PRI["PriorityPolicy\n同股票同分鐘：status 2 > status 1"]
    AGG["aggregate_stock_signals\n合併 methods_hit（去重 + 排序）"]
    COOL["CooldownPolicy\ncooldown key = stock_no + stock_status\n5 分鐘內同 key → 不發"]

    ANY{有可發送\n事件？}
    NOEV["本分鐘無觸發，結束"]

    IDEM["build_minute_idempotency_key\nkey = stock_no + minute_bucket\n（防同分鐘重複插入）"]
    RENDER["render_line_template_message\ntemplate=line_minute_digest_v1\ncontext = 全部可發送事件"]
    SEND["LinePushClient.push_message\n每分鐘只發 1 封"]

    SENDOK{推送成功？}
    LOG_FAIL["寫 system_logs ERROR\n**不寫 message 表**"]

    PERSIST["persist_message_rows_transactional\n單一 DB transaction\n全部股票事件一次 commit"]
    TXOK{Transaction\n成功？}
    LEDGER["寫 pending_delivery_ledger\n（或 fallback logs/pending_delivery.jsonl）\n補償期間視同已通知"]
    END["🏁 本分鐘結束"]

    TICK --> SESS
    SESS -- No --> SKIP_SESS --> END
    SESS -- Yes --> MKTCHECK
    MKTCHECK -- No / Timeout --> MKTFAIL --> END
    MKTCHECK -- Yes --> OPENCHK
    OPENCHK -- No --> OPENSEND --> WATCH
    OPENCHK -- Yes --> WATCH
    WATCH --> FETCH
    FETCH -- MaxRetry reached --> RETRY_EX --> END
    FETCH -- OK --> STALE
    STALE -- Yes --> STALE_SKIP --> END
    STALE -- No --> EVAL
    EVAL --> PRI --> AGG --> COOL --> ANY
    ANY -- No --> NOEV --> END
    ANY -- Yes --> IDEM --> RENDER --> SEND --> SENDOK
    SENDOK -- No --> LOG_FAIL --> END
    SENDOK -- Yes --> PERSIST --> TXOK
    TXOK -- Yes --> END
    TXOK -- No --> LEDGER --> END
```

---

## 3.2 冷卻 & 冪等說明

| 機制 | 鍵 | 作用 |
|---|---|---|
| **冷卻** | `stock_no + stock_status` | 5 分鐘內同鍵不重發；冷卻期不更新 `message.update_time` |
| **冪等** | `stock_no + minute_bucket` | 防同分鐘因重啟重複插入；使用 `INSERT ... ON CONFLICT DO UPDATE` |

---

## 3.3 跳過分鐘不補發規則

以下情境直接跳過，**不在後續分鐘補發過期訊號**：
- `MARKET_TIMEOUT` — 大盤資料逾時
- `STALE_QUOTE` — 行情 tick_at 超過 90 秒
- `DATA_CONFLICT` — 大盤資料衝突
- 重試耗盡（`MAX_RETRY_COUNT=3`）
