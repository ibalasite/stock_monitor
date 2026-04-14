# 05 — 雙行情來源 Freshness-First 聚合策略

> 對齊 EDD §3.3、§3.4、ADR-014。

---

## 5.1 設計動機

| 問題 | 解決方式 |
|---|---|
| TWSE `a` 欄位在兩筆成交間短暫為 `-` | `_price_cache` 保留最後已知委賣一；冷啟動以昨收 `y` 種子 |
| Yahoo Finance v8 API 有 20 分鐘延遲 | 改用 HTML scraping 抓委賣一（即時） |
| 單來源故障造成監控中斷 | `CompositeMarketDataProvider` 以 `tick_at` 判斷新鮮度，自動 fallback |

---

## 5.2 Freshness-First 聚合流程

```mermaid
flowchart TD
    START["CompositeMarketDataProvider\n.get_realtime_quotes(stock_nos)"]

    TWSE_CALL["TwseRealtimeMarketDataProvider\n.get_realtime_quotes()"]
    YF_CALL["YahooFinanceMarketDataProvider\n.get_realtime_quotes()"]

    subgraph TWSE_FLOW["TWSE 內部流程"]
        A_CHK{"a 欄位有值？\n(委賣五檔)"}
        A_OK["price = a.split('_')[0]\n更新 _price_cache\ntick_at = tlong // 1000"]
        A_EMPTY["price = _price_cache[stock_no]"]
        CACHE_CHK{"cache 有值？"}
        SEED["以 y（昨收）種子填 cache"]
        NO_TWSE["twse_quote = None"]

        A_CHK -- Yes --> A_OK
        A_CHK -- No/'-' --> A_EMPTY
        A_EMPTY --> CACHE_CHK
        CACHE_CHK -- Yes --> A_OK
        CACHE_CHK -- No --> SEED
        SEED --> A_OK
        SEED -- y 也無效 --> NO_TWSE
    end

    subgraph YAHOO_FLOW["Yahoo 內部流程"]
        HTTP_CHK{"HTTP 請求\n成功？"}
        HTML_PARSE["解析 HTML 委賣價區塊\n_RE_ASK 正規表示式"]
        ASK_CHK{"委賣一可解析？"}
        ASK_OK["price = 委賣一\ntick_at = regularMarketTime"]
        FB_PRICE["fallback: price = regularMarketPrice\n（盤後/休市）"]
        HTTP_FAIL["WARN log\nyahoo_quote = None"]

        HTTP_CHK -- Yes --> HTML_PARSE --> ASK_CHK
        ASK_CHK -- Yes --> ASK_OK
        ASK_CHK -- No --> FB_PRICE
        HTTP_CHK -- No --> HTTP_FAIL
    end

    TWSE_CALL --> TWSE_FLOW
    YF_CALL --> YAHOO_FLOW

    MERGE["Composite 合併邏輯\n（per stock_no）"]
    BOTH{"兩者皆有值？"}
    COMP_FRESH["比較 tick_at\n取較新者勝\n（相等時 TWSE 優先）"]
    EITHER{"至少一有值？"}
    USE_AVAIL["使用有值的那個"]
    STALE["回傳空\n→ 呼叫端觸發 STALE_QUOTE"]

    TWSE_FLOW --> MERGE
    YAHOO_FLOW --> MERGE
    MERGE --> BOTH
    BOTH -- Yes --> COMP_FRESH
    BOTH -- No --> EITHER
    EITHER -- Yes --> USE_AVAIL
    EITHER -- No --> STALE
```

---

## 5.3 三個 Adapter 職責對比

| Adapter | 端點 | price 來源 | 失敗行為 |
|---|---|---|---|
| `TwseRealtimeMarketDataProvider` | `mis.twse.com.tw/stock/api/getStockInfo.jsp` | `a` 欄位委賣一 → cache → `y` 種子 | 若無任何快取，不加入結果 |
| `YahooFinanceMarketDataProvider` | `tw.stock.yahoo.com/quote/{stock_no}` | HTML 委賣價區塊 → fallback `regularMarketPrice` | WARN log，回傳空 dict，**不 raise** |
| `CompositeMarketDataProvider` | 委派以上兩者 | Freshness-First（tick_at 較新者勝） | 兩者皆空 → STALE_QUOTE |

---

## 5.4 冷啟動行為說明

```
首次輪詢，_price_cache 為空：
  → a 有值 → 正常取值 → cache update
  → a 為 '-' → cache 空 → 以 y（昨收）種子填 cache → 使用昨收作為暫代
  → a 為 '-' 且 y 也無效 → 本次不加入結果（STALE_QUOTE）
```
