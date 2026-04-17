# User Story + 驗收條件（Stock Monitoring）

版本：v0.7  
日期：2026-04-17  
對應文件：`PDD_Stock_Monitoring_System.md`（v1.5）

## 1. 文件目的
將 PDD 轉成可排程、可實作、可驗收的 User Story，作為 PM/工程/QA 共用基線。

## 2. 優先級與分期
| Priority | Release | 說明 |
|---|---|---|
| P0 | M1 | 核心可用版：可監控、可通知、可去重、可觀測 |
| P1 | M2 | 一致性強化：補償與落盤恢復 |
| P2 | M2/M3 | 估值與策略擴充 |

## 3. User Stories

### US-001 監控清單管理
- Priority：`P0`
- Release：`M1`
- Dependency：`None`
- As 個人投資者, I want 維護監控股票與手動門檻, So that 系統可按我條件監控。

**Acceptance Criteria**
1. 可新增監控股票，必填 `stock_no/manual_fair_price/manual_cheap_price/enabled`。
2. 可停用與刪除監控股票。
3. `manual_cheap_price <= manual_fair_price`，否則拒絕寫入。
4. 僅 `enabled=1` 的股票進入盤中監控。

### US-002 盤中輪詢與交易時段判斷
- Priority：`P0`
- Release：`M1`
- Dependency：`US-001`
- As 使用者, I want 系統每分鐘僅在可交易時段抓價, So that 通知判斷正確。

**Acceptance Criteria**
1. 排程每 60 秒執行一次輪詢。
2. 週六、週日不輪詢。
3. 國定假日（政府行事曆）不輪詢。
4. 若大盤資料顯示當日無更新，視為不開盤，該分鐘跳過通知。
5. 資料源 timeout/不可用時，該分鐘不通知並記錄 WARN/ERROR。
6. 行情資料若超過新鮮度門檻（預設 90 秒）視為 stale，不觸發訊號。
7. 當分鐘因 timeout/stale 被跳過，不得補發過期分鐘訊號。

### US-003 訊號判斷與優先級
- Priority：`P0`
- Release：`M1`
- Dependency：`US-001`, `US-002`
- As 投資者, I want 收到有優先順序的買點訊號, So that 我先關注更重要的低價訊號。

**Acceptance Criteria**
1. `market_price <= fair_price` 產生 `stock_status=1`。
2. `market_price <= cheap_price` 產生 `stock_status=2`。
3. 同時符合 1 與 2 時，只保留 2。
4. 同股票同分鐘若先判到 1 再判到 2，最終輸出需為 2。

### US-004 LINE 彙總通知與效能 KPI
- Priority：`P0`
- Release：`M1`
- Dependency：`US-002`, `US-003`, `US-011`
- As 投資者, I want 同分鐘只收到一封彙總訊息且延遲可控, So that 我不會被洗訊息且能及時行動。

**Acceptance Criteria**
1. 每分鐘最多發 1 封 LINE 訊息。
2. 同分鐘多股票命中時，整併在同一封訊息。
3. 同分鐘多方法命中時，訊息含所有方法名稱。
4. 訊息至少包含：股票代碼、狀態、方法、觸發價、現價、時間。
5. 發送目標為使用者指定 `groupId`。
6. 通知延遲 KPI：觸發到發送成功 P95 <= 60 秒。
7. 通知準確率 KPI：>= 99%（排除資料源中斷分鐘）。

### US-005 冷卻規則
- Priority：`P0`
- Release：`M1`
- Dependency：`US-004`
- As 投資者, I want 相同訊號在 5 分鐘內不重複推送, So that 不會產生通知疲勞。

**Acceptance Criteria**
1. 冷卻維度為 `stock_no + stock_status`。
2. `update_time` 在 300 秒內，該訊號不發送。
3. 冷卻期間不更新既有 `message.update_time`。
4. `last_sent_at` 不存在（首次命中）時可發送。
5. 重複通知 KPI：相同 `stock_no + stock_status` 在 300 秒內重複發送數 = 0。

### US-006 訊息落盤一致性
- Priority：`P0`
- Release：`M1`
- Dependency：`US-004`, `US-005`
- As 維運者, I want 通知與資料庫狀態一致, So that 冷卻與追蹤判斷可信。

**Acceptance Criteria**
1. LINE 發送成功後，才寫入 `message`。
2. LINE 發送失敗時，不寫 `message`，只寫 system log。
3. 同分鐘全部 `message` 寫入需單一 transaction。
4. `message` 支援 `stock_no + minute_bucket` 去重。
5. 可儲存 `methods_hit` 以追蹤同分鐘多方法命中。

### US-007 補償機制
- Priority：`P1`
- Release：`M2`
- Dependency：`US-006`
- As 維運者, I want LINE 成功但 DB 失敗時可補償回補, So that 不漏帳且不重複通知。

**Acceptance Criteria**
1. 發生「LINE 成功、DB 失敗」時，寫入 `pending_delivery_ledger`（或 fallback 檔）。
2. 補償項狀態至少含 `PENDING/RECONCILED/FAILED`。
3. 補償未完成前，視同該分鐘已通知，不得重複推送。
4. 補償成功後，`message` 回補完成且 ledger 狀態為 `RECONCILED`。

### US-008 每日估值結算
- Priority：`P2`
- Release：`M2`
- Dependency：`US-002`
- As 投資者, I want 每交易日收盤後產生合理價與便宜價, So that 隔日可依最新估值監控。

**Acceptance Criteria**
1. 每交易日 14:00 執行估值任務一次。
2. 非交易日 14:00 不執行估值。
3. 估值失敗時，不覆蓋舊快照。
4. 快照至少含 `stock_no/method_name/method_version/trade_date/fair/cheap`。
5. 快照唯一鍵含 `method_version`。
6. 第一批方法固定包含 `emily_composite_v1`、`oldbull_dividend_yield_v1`、`raysky_blended_margin_v1`。

### US-009 多方法估值策略
- Priority：`P2`
- Release：`M2/M3`
- Dependency：`US-008`
- As 投資者, I want 多方法估值可開關, So that 可以擴充不同策略。

**Acceptance Criteria**
1. 方法以全域 `enable/disable` 控制。
2. 同 `method_name` 同時間只允許一個 `enabled` 版本。
3. 任一方法命中即可觸發通知。
4. 同分鐘同股票多方法命中時，訊息需列出方法清單。
5. 第一批方法命名與版本需與 PDD/EDD 一致，不得混用舊命名（如 `pe_band`/`pb_band`）。

### US-014 估值資料來源充分性
- Priority：`P2`
- Release：`M2/M3`
- Dependency：`US-008`, `US-009`
- As 投資者, I want 每日估值有可計算性與來源備援, So that 不會因單一資料缺失導致整體估值停擺。

**Acceptance Criteria**
1. 每方法需定義 `required_fields` 最小輸入集。
2. 每日估值對每檔每方法輸出狀態：`SUCCESS` / `SKIP_INSUFFICIENT_DATA` / `SKIP_PROVIDER_ERROR`。
3. 單方法 `SKIP` 不得阻斷其他方法執行。
4. 單方法 `SKIP` 不得覆蓋舊快照。
5. 主來源失敗時若備援可用，應切換並完成該方法估值，且記錄來源切換 log。
6. 財務估值資料實作來源：`FinMindFinancialDataProvider`（FinMind API + SQLite SWR Cache，TTL 15 天）；快取 miss 時呼叫 API，快取 fresh 時不呼叫 API，快取 stale 時立即回傳並背景刷新。
7. `FinMindFinancialDataProvider` 在 `db_path=None`（無 DB）時不 raise，直接呼叫 API 降級運作。
8. ETF 類股（無財報資料）：子法缺資料時跳過對應子法，不阻斷其他子法計算。

### US-015 開盤監控設定摘要通知
- Priority：`P1`
- Release：`M2`
- Dependency：`US-001`, `US-008`, `US-009`
- As 投資者, I want 開盤時先收到當日監控設定摘要, So that 我能先確認今日監控股票、方法與門檻價格。

**Acceptance Criteria**
1. 觸發時機為每交易日第一個可交易分鐘。
2. 同一交易日僅可發送一次摘要通知，重啟後不得重複發送。
3. 摘要需列出：監控股票清單、啟用方法清單（含 `manual_rule`）、逐股票逐方法 `fair/cheap`。
4. `manual_rule` 取自 `watchlist`，估值方法取自 `valuation_snapshots`（`trade_date <= today` 最新值）。
5. 若某股票某方法沒有可用快照，摘要中仍需列出方法並標示 `N/A`。
6. 摘要訊息需由模板渲染（Template-driven），不得將完整文案格式寫死於業務程式；需支援手機友善精簡格式（例如：`台積電(2330) 手動 2000/1500`）。

### US-016 全量 LINE 訊息模板化
- Priority：`P1`
- Release：`M2`
- Dependency：`US-004`, `US-015`
- As 投資者與維運者, I want 所有出站 LINE 訊息都由模板渲染, So that 可控文案一致且不需改主流程程式。

**Acceptance Criteria**
1. 每分鐘彙總通知必須透過 `template_key + context` 渲染，不得在業務層拼接最終文案。
2. 開盤監控摘要通知必須透過 `template_key + context` 渲染。
3. 單股觸發內容列（status 1/2）必須透過 `template_key + context` 渲染。
4. 若系統提供測試推播功能，測試推播文案也必須透過 `template_key + context` 渲染。
5. 任一模板缺失或渲染失敗需記錄明確錯誤，且不得默默退回未知硬編碼格式。

### US-010 觀測性與健康檢查
- Priority：`P0`
- Release：`M1`
- Dependency：`US-002`, `US-004`, `US-006`
- As 維運者, I want 能快速判斷系統是否可運作, So that 可以及時定位問題。

**Acceptance Criteria**
1. 啟動時檢查 SQLite `JSON1` 與 `foreign_keys=ON`。
2. 健康檢查可回報核心依賴狀態。
3. 關鍵失敗（資料源、LINE、DB）需有結構化 log。

### US-011 LINE 設定與密鑰驗證
- Priority：`P0`
- Release：`M1`
- Dependency：`None`
- As 使用者, I want 在啟動前驗證 LINE 設定, So that 上線後能立即正常通知。

**Acceptance Criteria**
1. 啟動前必檢 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LINE_TO_GROUP_ID`（可接受別名 `CHANNEL_ACCESS_TOKEN` / `TARGET_GROUP_ID`）。
2. token 無效或 groupId 無效時 fail-fast，並輸出可操作錯誤訊息。
3. 提供 `doctor` 或 health 診斷項顯示 LINE 連線結果。
4. secret/token 不得寫入明文日誌。

### US-012 重啟恢復與去重一致性
- Priority：`P0`
- Release：`M1`
- Dependency：`US-006`
- As 維運者, I want 服務重啟後仍維持去重一致性, So that 不會重複推送或漏帳。

**Acceptance Criteria**
1. 通知去重採 `idempotency_key = stock_no + minute_bucket`（不含 `stock_status`）。
2. 服務重啟後需先載入最近通知狀態再進入輪詢。
3. 同分鐘已發送事件在重啟後不得再次推送。
4. 補償中事件在重啟後須能續跑而非重送。

### US-013 行情來源品質控管
- Priority：`P1`
- Release：`M2`
- Dependency：`US-002`
- As 維運者, I want 對行情新鮮度與來源衝突做控管, So that 降低錯誤訊號。

**Acceptance Criteria**
1. quote 超過新鮮度門檻時標記 `STALE_QUOTE`，該分鐘不觸發。
2. 多來源報價價差超過門檻時標記 `DATA_CONFLICT`，該分鐘不觸發。
3. `STALE_QUOTE/DATA_CONFLICT` 都需記錄 WARN 與對應股票代碼。

### US-017 雙行情來源容錯
- Priority：`P1`
- Release：`M2`
- Dependency：`US-002`
- FR：`FR-15`
- As 系統管理者, I want 行情採雙來源（TWSE 主 + Yahoo 副）並以 Freshness-First 取捨, So that 單一來源故障時監控仍能持續運作。

**Acceptance Criteria**
1. TWSE MIS 為主來源（委賣一 `a` 欄位）；Yahoo Finance HTML scraping 為副來源。
2. 取捨規則：Yahoo 的 `regularMarketTime` 嚴格大於 TWSE cache 的 `tick_at` 時採 Yahoo，否則採 TWSE cache。
3. TWSE cache 為空（冷啟動）時直接採 Yahoo 值。
4. Yahoo 請求失敗（逾時 / HTTP 錯誤）時記錄 WARN，回退使用 TWSE cache，不中斷主流程。
5. 兩者均無效時標記 `STALE_QUOTE`，跳過該分鐘通知。

### US-018 行情 Adapter 可替換
- Priority：`P1`
- Release：`M2`
- Dependency：`US-017`
- FR：`FR-16`
- As 開發者, I want 行情 adapter 透過 Port 介面注入, So that 未來可不改 Application layer 替換任一 provider。

**Acceptance Criteria**
1. `MarketDataPort` 定義 `get_realtime_quotes(stock_nos) -> dict[str, dict]` 與 `get_market_snapshot(now_epoch) -> dict`。
2. `CompositeMarketDataProvider` 只依賴 `MarketDataPort` 介面，不直接依賴 TWSE 或 Yahoo 具體實作。
3. 替換任一 provider 不需修改 Application layer 程式碼。
4. Freshness-First 聚合邏輯集中在 `CompositeMarketDataProvider`，不散落各處。

### US-019 每日估值時儲存股票中文名稱
- Priority：`P1`
- Release：`M2`
- Dependency：`US-008`
- FR：`FR-18`
- As 使用者, I want 股票中文名稱在每日估值時一併存入 DB, So that 盤中通知顯示名稱不需每分鐘額外呼叫 API。

**Acceptance Criteria**
1. `watchlist` 資料表含 `stock_name TEXT NOT NULL DEFAULT ''` 欄位。
2. 每交易日 14:00 估值時，同步取得各股票中文名稱並以 UPDATE 寫入 `watchlist.stock_name`。
3. 盤中監控循環的 `stock_name_map` 一律由 `watchlist.stock_name` 提供，不從即時報價 `name` 欄位取得。
4. `watchlist.stock_name` 為空字串時，顯示 fallback 為股票代碼（如 `2330`）。
5. 名稱更新不引入每分鐘額外 API 呼叫。

### US-020 手動執行全市場估值掃描
- Priority：`P2`
- Release：`M3`
- Dependency：`US-008`, `US-019`
- FR：`FR-19`
- As 個人投資者, I want 一次掃描全體上市上櫃股票的三方法估值, So that 可自動找出低估股並更新監控清單。

**Acceptance Criteria**
1. CLI `scan-market` 成功執行時，從 TWSE/TPEX 取得全體普通股清單（含 stock_no, stock_name, market）。
2. 對每支股票嘗試三個估值方法；每方法回傳 SUCCESS 或 SKIP（附原因）。
3. `agg_fair_price` / `agg_cheap_price` 為所有 SUCCESS 方法的最高值（max）；若無 SUCCESS 方法則標記 uncalculable。
4. `yesterday_close <= agg_cheap_price` 的股票：upsert watchlist（更新 stock_name/fair/cheap；不強制改 `enabled`）；upsert 後可區分 `watchlist_new`（本次新增）與 `watchlist_updated`（本次更新）兩計數。
5. `agg_cheap_price < yesterday_close <= agg_fair_price` 的股票：輸出 `scan_YYYYMMDD_near_fair.csv`。
6. 無法計算的股票：輸出 `scan_YYYYMMDD_uncalculable.csv`；skip 原因內嵌於 `methods_skipped` 欄（格式 `method:reason`），無獨立 `skip_reasons` 欄位。
7. 所有輸出檔欄位：`stock_no, stock_name, agg_fair_price, agg_cheap_price, yesterday_close, methods_success, methods_skipped`。
8. 完成後以 stdout 印出摘要統計（不發送 LINE）。
9. TWSE/TPEX 清單擷取 retry 3 次後失敗：CLI 輸出錯誤訊息並以非 0 exit code 結束。
10. 個別股票計算例外不中斷整體掃描；寫入 `system_logs`（level=ERROR, event=MARKET_SCAN_STOCK_ERROR）。
11. `scan-market` 執行前必須從 DB 載入 `valuation_methods.enabled=1` 方法清單並注入掃描，不可硬編碼空方法清單。
12. 若啟用方法數為 0，CLI 必須 fail-fast（非 0 exit code），不得以「全數 uncalculable」視為成功。
13. 估值計算資料來源為 `ParallelFinancialDataProvider`（`db_path` 透過 `load_enabled_scan_methods` 傳入），三源（FinMind P1、MOPS P2、Goodinfo P3）同時觸發；各源獨立 SWR Cache，回傳 `fetched_at` 最新者的值。

### US-022 財務資料三源平行備援
- Priority：`P2`
- Release：`M3`
- Dependency：`US-020`
- FR：`FR-21`
- As 個人投資者, I want 財務資料從三個來源同時取得, So that 任一來源失效時不影響估值結果。

**Acceptance Criteria**
1. 系統必須同時（非序列）觸發 FinMind（P1）、MOPS/TWSE（P2）、Goodinfo（P3）三個財務資料來源。
2. 各源有獨立的 SQLite SWR Cache（`financial_data_cache.provider` 欄位區分 `'finmind'`/`'mops'`/`'goodinfo'`）；任一源的快取不干擾其他源。
3. 三源全部完成後，以 `fetched_at` 最新者的值提供給估值方法；若多源 `fetched_at` 相同則任一可用。
4. 任一源回傳 `ProviderUnavailableError`（API 失敗、scraping timeout、rate limit），其他源不受影響，估值仍可進行。
5. 三源全部 `ProviderUnavailableError` 時，估值方法記錄 `SKIP_INSUFFICIENT_DATA`，不中斷整體掃描。
6. SWR Cache miss（DB 無記錄）時，**同步**取資料並寫 DB 後回傳；不得以背景非阻塞替代。
7. SWR Cache stale（有記錄但已過 `SWR_TTL_SECONDS`）時，立即回傳舊值並同時觸發**背景**刷新；不得阻塞等待刷新完成。
8. `_fetch_raw` 回傳 `None`（確認無此股票任何資料）時，不寫入快取，raise `ProviderUnavailableError`。
9. 三個 Adapter 的 `provider_name` 分別固定為 `'finmind'`、`'mops'`、`'goodinfo'`，不得重複。

### US-021 macOS / Windows 雙平台相容
- Priority：`P1`
- Release：`M2`
- Dependency：`US-001`
- FR：`FR-20`
- As 個人投資者（使用 macOS 開發機）, I want 系統在 macOS 與 Windows 均可完整運作, So that 開發環境切換不影響功能正確性。

**Acceptance Criteria**
1. 在 macOS 上執行 `python -m pytest -q tests` 全部通過，coverage 100%。
2. `scripts/start_daemon.sh` 執行後，daemon 在背景啟動並在 `logs/daemon.pid` 寫入 PID。
3. `scripts/stop_daemon.sh` 執行後，送出 SIGTERM，daemon 在當前分鐘週期結束後乾淨退出，無殘留行程。
4. `plutil -lint scripts/com.stock_monitor.daemon.plist` 回傳無錯誤。
5. 所有生產程式碼（`stock_monitor/` 下）中不存在 `os.path.join`、`"/"+`、`"\\"+` 等硬編碼路徑分隔符；一律使用 `pathlib.Path`。
6. `signal.SIGTERM` handler 安裝前必須有 `sys.platform != "win32"` 判斷；在 Windows 模擬環境下不引發 `AttributeError`。

## 4. 與 PDD UAT 對照
1. UAT-1 對應 `US-003/US-004/US-005`。
2. UAT-2 對應 `US-005`。
3. UAT-3 對應 `US-006`。
4. UAT-4 對應 `US-002`。
5. UAT-5 對應 `US-008`。
6. UAT-6 對應 `US-004/US-009`。
7. UAT-7 對應 `US-003`。
8. UAT-8 對應 `US-007/US-012`。
9. UAT-12 對應 `US-008/US-009/US-014`。
10. UAT-13 對應 `US-015`。
11. UAT-14 對應 `US-016`。
12. UAT-15 對應 `US-017`。
13. UAT-16 對應 `US-020`。
14. UAT-17 對應 `US-021`。
15. UAT-18（新）對應 `US-022`（三源平行財務資料備援）。

## 5. BDD 拆分建議
1. `P0` 先建 `.feature`：`US-011 -> US-001 -> US-002 -> US-003 -> US-004 -> US-005 -> US-006 -> US-012 -> US-010`。
2. `P1/P2` 再擴：`US-007`, `US-013`, `US-008`, `US-009`, `US-014`, `US-020`, `US-022`。
3. 每個 Acceptance Criteria 至少一個 Scenario。
