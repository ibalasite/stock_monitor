# API_CONTRACT - Stock Monitoring System

版本：v0.4  
日期：2026-04-17  
來源基準：`PDD_Stock_Monitoring_System.md`（v1.3）、`EDD_Stock_Monitoring_System.md`（v1.3）

## 1. 文件目的
定義應用層與基礎設施層的介面契約，讓 BDD 與 TDD 可直接依契約落地測試與實作。

## 2. 全域常數契約
| Key | Value | 說明 |
|---|---|---|
| `APP_TZ` | `Asia/Taipei` | 系統時區 |
| `PRICE_CHECK_INTERVAL_SEC` | `60` | 每分鐘輪詢 |
| `NOTIFY_COOLDOWN_MIN` | `5` | 冷卻時間 |
| `MAX_RETRY_COUNT` | `3` | 行情重試上限 |
| `STALE_THRESHOLD_SEC` | `90` | 報價新鮮度門檻 |
| `TRADING_START` | `09:00` | 交易開始 |
| `TRADING_END` | `13:30` | 交易結束 |
| `DAILY_VALUATION_TIME` | `14:00` | 日結估值時間 |
| `OPEN_CHECK_START` | `08:45` | 開盤資料檢查起始 |

## 3. 設定契約（LINE）
1. Canonical:
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_TO_GROUP_ID`
2. Legacy alias:
   - `CHANNEL_ACCESS_TOKEN`
   - `TARGET_GROUP_ID`
3. 規則：
   - 啟動時 fail-fast 驗證必要值存在與格式有效。
   - Canonical 與 alias 同時存在時，以 Canonical 為準。
   - log 不得輸出完整 token。
4. 模板設定（FR-14）：
   - `LINE_TEMPLATE_DIR`
   - `LINE_TEMPLATE_MINUTE_DIGEST`
   - `LINE_TEMPLATE_TRIGGER_ROW`
   - `LINE_TEMPLATE_OPENING_SUMMARY`
   - `LINE_TEMPLATE_TEST_PUSH`（若系統提供測試推播）

## 4. Domain 事件契約
### 4.1 StockSignalEvent
| 欄位 | 型別 | 說明 |
|---|---|---|
| `stock_no` | `str` | 股票代碼 |
| `minute_bucket` | `str` | `YYYY-MM-DD HH:mm`（Asia/Taipei） |
| `stock_status` | `int` | `1`=below_fair, `2`=below_cheap |
| `methods_hit` | `list[str]` | 去重且排序後的方法清單 |
| `market_price` | `Decimal` | 現價 |
| `fair_price` | `Decimal` | 合理價 |
| `cheap_price` | `Decimal` | 便宜價 |
| `trigger_time_utc` | `int` | epoch seconds |

### 4.2 MinuteDigest
| 欄位 | 型別 | 說明 |
|---|---|---|
| `minute_bucket` | `str` | 分鐘桶 |
| `events` | `list[StockSignalEvent]` | 該分鐘可發送事件 |
| `message_text` | `str` | LINE 單封彙總訊息內容（由 template 渲染） |
| `template_key` | `str` | 渲染使用模板鍵（例如 `line_minute_digest_v1`） |
| `idempotency_key` | `str` | `minute_bucket` 層級請求鍵（發送層） |

### 4.3 MarketScanResult（FR-19）
| 欄位 | 型別 | 說明 |
|---|---|---|
| `scan_date` | `str` | 掃描日期（`YYYYMMDD`） |
| `total_stocks` | `int` | 本次掃描股票總數 |
| `watchlist_upserted` | `int` | upsert 進 watchlist 的股票數（低於便宜價） |
| `watchlist_new` | `int` | 本次新增（原本不在 watchlist）的股票數 |
| `watchlist_updated` | `int` | 本次更新（原本已在 watchlist）的股票數 |
| `near_fair_count` | `int` | 高於便宜價但低於合理價的股票數（輸出 near_fair CSV） |
| `uncalculable_count` | `int` | 全方法無法計算的股票數（輸出 uncalculable CSV） |
| `above_fair_count` | `int` | 高於合理價的股票數（不輸出 CSV） |
| `output_dir` | `str` | CSV 輸出目錄路徑 |

不變式：`watchlist_new + watchlist_updated == watchlist_upserted`。

CSV 共用欄位（三份 CSV 均適用）：`stock_no`, `stock_name`, `agg_fair_price`, `agg_cheap_price`, `yesterday_close`, `methods_success`, `methods_skipped`。
- `methods_success`：成功計算方法名稱，`|` 分隔，如 `emily_composite_v1|oldbull_dividend_yield_v1`。
- `methods_skipped`：跳過方法與原因，格式 `method:reason`，`|` 分隔，如 `raysky_blended_margin_v1:SKIP_INSUFFICIENT_DATA`。無獨立 `skip_reasons` 欄位。

### 4.3 OutboundLineMessage
| 欄位 | 型別 | 說明 |
|---|---|---|
| `message_type` | `str` | `minute_digest` / `opening_summary` / `trigger_row` / `test_push` |
| `template_key` | `str` | 對應模板鍵 |
| `context` | `dict` | 模板渲染輸入資料 |
| `rendered_text` | `str` | render 後最終文案（不得為空） |

## 5. Port 契約
### 5.1 `MarketDataPort`
1. `fetch_quotes(stock_nos: list[str], now_utc: int) -> list[Quote]`
2. `fetch_market_index(now_utc: int) -> MarketIndexQuote`
3. Quote 欄位：
   - `stock_no: str`
   - `price: Decimal`
   - `quote_ts_utc: int`
   - `provider: str`
4. 錯誤碼：
   - `MARKET_TIMEOUT`
   - `PROVIDER_UNAVAILABLE`
   - `STALE_QUOTE`
   - `DATA_CONFLICT`

### 5.2 `TradingCalendarPort`
1. `is_trading_day(trade_date_local: str) -> bool`
2. `is_market_session(now_local: str, market_index_quote: MarketIndexQuote|None) -> bool`
3. 規則：
   - 週末與政府假日為 false。
   - 09:00 後若無當日大盤新資料，為 false。

### 5.3 `LineMessagingPort`
1. `send_text(message_type: str, message_text: str) -> LineSendResult`
2. `message_text` 必須是模板 render 的輸出，不可為業務層硬編碼完整文案。
3. `LineSendResult`：
   - `success: bool`
   - `provider_message_id: str|None`
   - `sent_at_utc: int|None`
   - `error_code: str|None`
   - `error_message: str|None`

### 5.4 `ValuationMethodPort`
1. `compute(stock_no: str, trade_date_local: str) -> ValuationResult`
2. `ValuationResult`：
   - `stock_no`
   - `method_name`
   - `method_version`
   - `fair_price`
   - `cheap_price`
3. 契約：
   - `cheap_price <= fair_price`。
   - 計算失敗不得覆蓋舊快照。

### 5.5 `MessageRepositoryPort`
1. `get_last_sent_at(stock_no: str, stock_status: int) -> int|None`
2. `upsert_minute_messages(events: list[StockSignalEvent], now_utc: int) -> None`
3. `list_by_minute(minute_bucket: str) -> list[MessageRow]`
4. 規則：
   - 冷卻鍵為 `stock_no + stock_status`。
   - 冪等鍵為 `stock_no + minute_bucket`（不含 status）。
   - `methods_hit` 必須落盤為 JSON array string。

### 5.6 `PendingDeliveryLedgerPort`
1. `append_pending(minute_bucket: str, payload_json: str, now_utc: int) -> None`
2. `list_pending(limit: int) -> list[PendingItem]`
3. `mark_reconciled(id: int, now_utc: int) -> None`
4. `mark_failed(id: int, error: str, now_utc: int) -> None`

### 5.7 `MessageTemplatePort`
1. `render(template_key: str, context: dict) -> str`
2. 契約：
   - `template_key` 不存在時拋 `TEMPLATE_NOT_FOUND`
   - template 語法或 context 缺失導致渲染失敗時拋 `TEMPLATE_RENDER_FAILED`
   - render 輸出不可為空字串
   - 所有出站 LINE 訊息（minute digest/opening summary/trigger row/test push）都必須先經過此介面

### 5.8 `AllListedStocksPort`（FR-19）
1. `get_all_listed_stocks() -> list[dict]`
2. 回傳格式：`[{"stock_no": str, "stock_name": str, "yesterday_close": float|None, "market": "TWSE"|"TPEx"}, ...]`
3. 契約：
   - HTTP 失敗時 retry 3 次後拋例外（不靜默吞掉）。
   - 回傳清單不得為空；若為空視為 fetch 失敗拋例外。
   - 只回傳普通股（排除 ETF、特別股、債券）。
4. 預設實作：`TwseAllListedStocksProvider`（`stock_monitor/adapters/all_listed_stocks_twse.py`）。

### 5.9 `scan-market` CLI 注入契約（FR-19）
1. CLI 在呼叫 `run_market_scan_job` 前，必須組出 `valuation_methods`（由 DB `valuation_methods.enabled=1` 載入）。
2. 禁止傳入空清單 `valuation_methods=[]` 作為正常掃描路徑。
3. 若啟用方法數為 0，CLI 應 fail-fast 並回傳錯誤，不輸出 CSV。

## 6. 錯誤語意契約
| Error Code | 行為 |
|---|---|
| `CONFIG_INVALID` | 啟動 fail-fast |
| `MARKET_TIMEOUT` | 該分鐘跳過，不補發，寫 WARN |
| `STALE_QUOTE` | 該股票該分鐘跳過，不補發，寫 WARN |
| `DATA_CONFLICT` | 該股票該分鐘跳過，不補發，寫 WARN |
| `LINE_SEND_FAILED` | 不寫 `message`，寫 ERROR |
| `DB_WRITE_FAILED_AFTER_SEND` | 寫補償佇列，視同已通知 |
| `TEMPLATE_NOT_FOUND` | 寫 ERROR，該次通知視為失敗 |
| `TEMPLATE_RENDER_FAILED` | 寫 ERROR，該次通知視為失敗 |
| `MARKET_SCAN_STOCK_ERROR` | 寫 ERROR（level），繼續下一支股票，不中斷整體掃描 |
| `MARKET_SCAN_LIST_FETCH_FAILED` | scan-market fail-fast，印出錯誤後退出 |
| `MARKET_SCAN_METHODS_EMPTY` | 無 `enabled=1` 估值方法時 fail-fast 退出，不輸出 CSV |

## 7. BDD 對應
1. `features/stock_monitoring_system.feature` 的 `TP-ENV-*`、`TP-POL-*`、`TP-INT-*` 全部應可對應到本契約至少一個 Port 行為。
2. 新增功能時，先改本文件，再改 `.feature` 與 `TEST_PLAN`，最後寫測試與程式。
