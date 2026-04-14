# 02 — Clean Architecture 層次圖（C4 Level 2）

> **C4 L2** 描述系統內部的模組分層與依賴方向。  
> 對齊 EDD §3.1、ADR-001（Clean Architecture 分層）。

---

## 2.1 分層原則

```
外層依賴內層，內層不知道外層存在。
Domain 是核心，不依賴任何 adapter 或框架。
```

| 層次 | 職責 | 禁止 |
|---|---|---|
| **Interface** | CLI 入口、Daemon loop 組裝 | 不可含業務邏輯 |
| **Application** | Use Case / 流程協調、模板渲染 | 不可直接操作 DB 或 HTTP |
| **Domain** | 純業務規則（Policy、Idempotency、TimeBucket） | 不可 import infra 或 application |
| **Infrastructure** | Adapter 實作（HTTP、DB、LINE） | 不可含業務規則 |

---

## 2.2 模組依賴圖

```mermaid
flowchart TD
    subgraph Interface["Interface Layer"]
        APP_PY["app.py\nCLI entry points\n(init-db / run-once / run-daemon ...)"]
        DAEMON["daemon_runner.py\nDI 組裝 + Daemon loop"]
    end

    subgraph Application["Application Layer"]
        MW["monitoring_workflow.py\naggregate_minute_notifications\ndispatch_and_persist_minute\nreconcile_pending_once"]
        VS["valuation_scheduler.py\nrun_daily_valuation_job"]
        VC["valuation_calculator.py\nManualValuationCalculator"]
        MT["message_template.py\nrender_line_template_message ← 唯一定義"]
        TS["trading_session.py\nevaluate_market_open_status\nis_in_trading_session"]
        RS["runtime_service.py\nMinuteCycleConfig"]
    end

    subgraph Domain["Domain Layer"]
        POL["policies.py\nPriorityPolicy\nCooldownPolicy\naggregate_stock_signals"]
        IDEM["idempotency.py\nbuild_minute_idempotency_key"]
        TB["time_bucket.py\nTimeBucketService\nguard_bucket_source"]
        METR["metrics.py\ncompute_notification_accuracy"]
    end

    subgraph Infrastructure["Infrastructure Layer"]
        subgraph Adapters["Adapters"]
            TWSE["market_data_twse.py\nTwseRealtimeMarketDataProvider\n(_price_cache / _exchange_cache / _tick_cache)"]
            YF["market_data_yahoo.py\nYahooFinanceMarketDataProvider"]
            COMP["market_data_composite.py\nCompositeMarketDataProvider\n(Freshness-First)"]
            LINE_A["line_messaging.py\nLinePushClient"]
        end
        subgraph DB["DB / Persistence"]
            SCHEMA["db/schema.py\nSCHEMA_SQL"]
            REPO["adapters/sqlite_repo.py\nSqliteWatchlistRepository\nSqliteMessageRepository\nSqliteValuationRepository\nSqliteSystemLogRepository"]
        end
        subgraph Bootstrap["Bootstrap"]
            BOOT_RT["bootstrap/runtime.py\nassert_sqlite_prerequisites\nvalidate_line_runtime_config"]
            BOOT_H["bootstrap/health.py\nhealth_check"]
        end
        subgraph UAT["UAT"]
            UAT_S["uat/scenarios.py\nUAT_SCENARIOS"]
        end
    end

    Interface --> Application
    Interface --> Infrastructure
    Application --> Domain
    Application --> Infrastructure
    COMP --> TWSE
    COMP --> YF

    classDef interface fill:#dbe9f4,stroke:#5b9bd5
    classDef application fill:#e2f0d9,stroke:#70ad47
    classDef domain fill:#fff2cc,stroke:#ffc000
    classDef infra fill:#fce4d6,stroke:#ed7d31

    class APP_PY,DAEMON interface
    class MW,VS,VC,MT,TS,RS application
    class POL,IDEM,TB,METR domain
    class TWSE,YF,COMP,LINE_A,SCHEMA,REPO,BOOT_RT,BOOT_H,UAT_S infra
```

---

## 2.3 關鍵架構規則（CR 改善項）

| 規則 | 描述 | 來源 |
|---|---|---|
| CR-ARCH-01 | 估值計算邏輯在 `application/valuation_calculator.py`，不在 `app.py` | ADR |
| CR-ARCH-03 | `render_line_template_message` 只在 `message_template.py` 定義一次 | EDD §7.6 |
| CR-ARCH-04 | DI 組裝在 `daemon_runner.py`，`app.py` 只做 CLI parse | ADR |
| CR-SEC-01 | `LinePushClient.channel_access_token` 加 `field(repr=False)` | EDD §7.1 |
| CR-SEC-03 | 無效時區名稱立即 `raise ValueError`，禁止靜默 fallback UTC | ADR |
