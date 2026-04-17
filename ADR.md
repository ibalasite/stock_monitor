# ADR - Architecture Decision Records

版本：v0.6  
日期：2026-04-17  
來源基準：`PDD_Stock_Monitoring_System.md`（v1.5）、`EDD_Stock_Monitoring_System.md`（v1.6）

## ADR-001 使用 Clean Architecture 分層
1. 狀態：Accepted
2. 決策：
   - Domain / Application / Infrastructure / Interface 分層。
3. 原因：
   - 估值方法與資料源需可插拔。
4. 影響：
   - 測試可先 mock Port 做 TDD。

## ADR-002 每分鐘單封彙總通知
1. 狀態：Accepted
2. 決策：
   - 同分鐘多股票/多方法命中只發 1 封 LINE。
3. 原因：
   - 降低訊息噪音並符合使用者需求。
4. 影響：
   - 需先聚合再發送，且 message 落盤採同分鐘批次。

## ADR-003 狀態優先序 `2 > 1`
1. 狀態：Accepted
2. 決策：
   - 同股票同分鐘若同時命中，僅通知 `status=2`。
3. 原因：
   - `below_cheap` 比 `below_fair` 重要。
4. 影響：
   - upsert 需支援同分鐘升級。

## ADR-004 冷卻鍵與冪等鍵分離
1. 狀態：Accepted
2. 決策：
   - 冷卻鍵：`stock_no + stock_status`
   - 冪等鍵：`stock_no + minute_bucket`
3. 原因：
   - 避免語意混用造成重複通知或錯擋。
4. 影響：
   - 需同時實作冷卻查詢與同分鐘唯一鍵約束。

## ADR-005 LINE 成功但 DB 失敗採補償佇列
1. 狀態：Accepted
2. 決策：
   - 發生「先送達、後落盤失敗」時寫 `pending_delivery_ledger`（或 jsonl fallback）。
3. 原因：
   - 保證不重複通知且可最終一致。
4. 影響：
   - 需要補償 worker 與重試狀態機。

## ADR-006 SQLite 作為單人版儲存
1. 狀態：Accepted
2. 決策：
   - Phase 1/2 以 SQLite 為主要資料庫。
3. 原因：
   - 單人使用、部署簡單、開發速度快。
4. 影響：
   - 不涵蓋多人高併發。

## ADR-007 `methods_hit` 必須為 JSON array
1. 狀態：Accepted
2. 決策：
   - schema 以 `json_valid` + `json_type='array'` 強制。
3. 原因：
   - 防止字串格式漂移，利於測試與回放。
4. 影響：
   - 所有寫入前需做去重/排序/序列化。

## ADR-008 LINE 參數命名採 Canonical + Legacy alias
1. 狀態：Accepted
2. 決策：
   - Canonical：`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TO_GROUP_ID`
   - Alias：`CHANNEL_ACCESS_TOKEN`、`TARGET_GROUP_ID`
3. 原因：
   - 與舊設定相容，同時明確新規範。
4. 影響：
   - 啟動檢核需支援兩組鍵並定義優先序。

## ADR-009 全市場行情清單採獨立 AllListedStocksPort
1. 狀態：Accepted
2. 決策：
   - 新增 `AllListedStocksPort` 抽象介面，預設實作為 `TwseAllListedStocksProvider`。
   - 主清單取自 TWSE（上市）與 TPEX（上櫃）公開 API，retry 上限 3 次。
   - 此 Port 與現有 `MarketDataPort` 完全分離，只負責「所有股票代碼清單」。
3. 原因：
   - FR-19 掃描範圍是全市場，不適合走既有 per-stock 的 `MarketDataPort`。
   - 抽象介面允許測試以 stub 取代，不必真實呼叫外部 API。
4. 影響：
   - 新增 `stock_monitor/adapters/all_listed_stocks_twse.py`。
   - 測試 symbol：`TwseAllListedStocksProvider`。

## ADR-009 BDD + Spec-Driven 流程先規格後實作
1. 狀態：Accepted
2. 決策：
   - 文件變更順序固定：
     - `PDD/EDD` -> `API_CONTRACT` -> `.feature` -> `TEST_PLAN` -> tests -> code
3. 原因：
   - 防止需求與測試漂移。
4. 影響：
   - PR 需附規格追蹤證據（至少對應 TP-ID / Scenario-ID）。

## ADR-010 LINE 訊息文案採 Template-driven Rendering
1. 狀態：Accepted
2. 決策：
   - minute digest 與 opening summary 訊息都改為「模板渲染」。
   - 主流程僅提供 context，不在業務程式拼接完整文案格式。
3. 原因：
   - 文案與排版需要頻繁調整（桌機/手機可讀性差異）。
   - 降低格式調整時修改核心流程程式的風險。
4. 影響：
   - 需新增模板設定鍵與 `MessageTemplatePort`。
   - 渲染失敗需有明確 `TEMPLATE_*` 錯誤語意與測試覆蓋。

## ADR-011 估値計算器邏輯放在 Application 層
1. 狀態：Accepted
2. 決策：
   - `ManualValuationCalculator`（及後續估値方法）需位於 `stock_monitor/application/` 下，不可定義在 CLI 入口 `app.py`。
3. 原因：
   - `app.py` 是 CLI Interface 層，職責將僅為解析命令列參數與呼叫 Application 服務（SRP 原則）。
   - 估値邏輯放在 `app.py` 導致測試需啟動 CLI，增加耦合（CR-ARCH-01）。
4. 影響：
   - 新增 `stock_monitor/application/valuation_calculator.py`，`app.py` 僅做依賴注入。
   - CLAUDE.md §11 禁止清單已登錄此規則。

## ADR-012 `render_line_template_message` 唯一定義於 `message_template`
1. 狀態：Accepted
2. 決策：
   - 所有出站 LINE 訊息文案渲染只能呼叫 `stock_monitor.application.message_template.render_line_template_message`，禁止在其他模組重複定義。
3. 原因：
   - 重複定義導致文案格式分歧，難以維護（CR-ARCH-03）。
4. 影響：
   - `monitoring_workflow.py`、`app.py` 等模組必須 import，不可 inline 定義。
   - CLAUDE.md §11 禁止清單已登錄此規則（CR-ARCH-03）。

## ADR-013 開盤摘要冪等狀態以 DB 欄位記錄
1. 狀態：Accepted
2. 決策：
   - 開盤摘要是否已送出的判斷依據為 DB 欄位（如 `system_logs` 或專屬欄位），不得以程式記憶體變數或日誌字串比對判斷。
3. 原因：
   - `daemon` 重啟後記憶體狀態清空，若依賴記憶體變數或 log 字串，重啟後 09:01 將無法補送已錯過的開盤摘要（CR-ARCH-06）。
4. 影響：
   - 每次 `evaluate_market_open_status` 前必須查詢 DB，確認當日是否已送出。
   - CR-CODE-06 對應實作改善：開盤檢查起始時間 08:45～09:00 區間唇均可評估，不定式限定在整分点 09:00。

## ADR-016 FR-19 watchlist upsert 採 SELECT-before-upsert 以區分新增 vs 更新
1. 狀態：Accepted
2. 決策：
   - 掃描到符合便宜價的股票執行 watchlist upsert 前，先執行 `SELECT 1 FROM watchlist WHERE stock_no = ?` 判斷是否已存在。
   - 已存在 → `watchlist_updated` 計數 +1；不存在 → `watchlist_new` 計數 +1。
   - `MarketScanResult` 新增三個純量欄位：`watchlist_upserted`（總數）、`watchlist_new`（本次新增）、`watchlist_updated`（本次更新）。
   - 不變式：`watchlist_new + watchlist_updated == watchlist_upserted`。
3. 原因：
   - PDD §14 要求 watchlist_added.csv 內容（新增 vs 更新）可追溯，純 upsert 計數無法滿足。
   - SELECT-before-upsert 在 SQLite 上成本極低（主鍵索引查詢），無需額外異動 schema 或引入 RETURNING 子句（SQLite 版本相容性考量）。
   - 相較 A2 方案（UPSERT RETURNING + 比較 changes()），A1 邏輯更清晰且無平台版本依賴。
4. 影響：
   - `market_scan.py` 的 upsert 區段需在每次 upsert 前加一次 SELECT 判斷。
   - `MarketScanResult` dataclass 新增 `watchlist_new: int = 0` 與 `watchlist_updated: int = 0`。
   - `scan_YYYYMMDD_watchlist_added.csv` 輸出欄位不變，但後端計數拆分為兩欄位供測試斷言。
   - `API_CONTRACT.md` §4.3 同步更新（v0.4）。

## ADR-015 跨平台路徑操作採 pathlib.Path；SIGTERM 處理需平台判斷
1. 狀態：Accepted
2. 決策：
   - 所有生產程式碼的檔案路徑操作一律使用 `pathlib.Path`，禁止字串拼接 `/` 或 `\`（CR-PLAT-01）。
   - daemon 啟動時安裝 SIGTERM handler，但需以 `sys.platform != "win32"` 為前提（Windows 不支援 SIGTERM，CR-PLAT-02）。
   - macOS 提供 `start_daemon.sh` / `stop_daemon.sh`（bash）；Windows 沿用 `start_daemon.ps1` / `stop_daemon.ps1`（PowerShell）。
   - launchd plist 作為範本提供（`scripts/com.stock_monitor.daemon.plist`），不自動安裝。
3. 原因：
   - `pathlib.Path` 在 Windows / macOS / Linux 均正確處理路徑分隔符，消除跨平台路徑 bug。
   - SIGTERM 是 Unix-only 訊號；在 Windows 呼叫 `signal.signal(signal.SIGTERM, ...)` 會拋出 `AttributeError`，必須有平台判斷。
   - 兩套腳本（sh / ps1）各自使用平台原生語法，比跨平台 wrapper 更易維護。
4. 影響：
   - `daemon_runner.py` 新增 `_install_signal_handlers(stop_event)`（EDD §15.6 Symbol Contract）。
   - CLAUDE.md / CODEX.md §12 禁止清單登錄 CR-PLAT-01、CR-PLAT-02、CR-PLAT-03。
   - 新增腳本：`scripts/start_daemon.sh`、`scripts/stop_daemon.sh`、`scripts/com.stock_monitor.daemon.plist`。

## ADR-014 雙行情來源採 Freshness-First 策略，行情 price 代表委賣一
1. 狀態：Accepted
2. 決策：
   - 盤中行情以 TWSE MIS（`getStockInfo.jsp`）為主，Yahoo Finance TW 頁面 HTML scraping 為副。
   - 合併邏輯由 `CompositeMarketDataProvider` 負責：比較兩來源的 `tick_at`（unix seconds），取較新者；相等時以 TWSE 為準。
   - **行情 `price` 代表委賣一（最佳委賣價，即當下可立即買到的最低價格）**，而非成交價。
   - TWSE：解析 `a` 欄位（委賣五檔，`_` 分隔），取第一個值。`a` 為空或 `-` 時以 `_price_cache` 補全；cache 冷時以 `y`（昨收）種子填充。
   - Yahoo：從 HTML 委賣價區塊解析委賣一；若該區塊不存在（盤後/休市）fallback `regularMarketPrice`。
3. 原因：
   - 委賣一代表**當下可立即買入的市場價**，比最後成交價更即時且穩定：成交價 `z` 在兩筆成交之間顯示 `-`，委賣一則持續存在於委託簿中。
   - TWSE `getStockInfo.jsp` 的 `a` 欄位與網站委賣五檔直接對應，數據一致。
   - Yahoo Finance TW HTML scraping 為近即時（秒級）；v8 chart API 有 ~20 分鐘強制延遲，已改用 HTML scraping。
   - Yahoo 的時間來源（`regularMarketTime`）以秒為單位，TWSE 為毫秒；兩者比較前統一換算為 unix seconds。
4. 影響：
   - 新增 `YahooFinanceMarketDataProvider`（`stock_monitor/adapters/market_data_yahoo.py`）。
   - 新增 `CompositeMarketDataProvider`（`stock_monitor/adapters/market_data_composite.py`）。
   - `TwseRealtimeMarketDataProvider` 新增 `_price_cache`（現儲存最後已知委賣一）、`_exchange_cache`，並在 `get_realtime_quotes` 回傳欄位中加入 `exchange`。
   - `daemon_runner.py` 的 `_build_runtime` 改為注入 `CompositeMarketDataProvider`。
   - CLAUDE.md §11 禁止清單登錄 CR-ADP-01、CR-ADP-02。

## ADR-017 財務估值資料採 FinMind API + SQLite SWR Cache（三層策略）
1. 狀態：Accepted
2. 決策：
   - 財務估值資料（股利、EPS、資產負債表、PE/PB 統計、歷年股價）統一由 `FinMindFinancialDataProvider` 提供，API 來源為 FinMind（`https://api.finmindtrade.com/api/v4/data`）。
   - 採 **Stale-While-Revalidate（SWR）** 三層快取策略：
     1. L1 記憶體（`_mem`，同進程，永不過期）
     2. L2 DB 新鮮（`financial_data_cache`，`fetched_at ≤ 15 天`）→ 直接回傳 + 升入 L1
     3. L3 DB 陳舊（`> 15 天`）→ 立即回傳舊值 + 背景執行緒刷新（去重 `_refreshing` set + `threading.Lock()`）
     4. L4 DB miss → API 擷取 → 寫 DB → 升入 L1
   - `db_path` 透過 `load_enabled_scan_methods(db_path=...)` → `FinMindFinancialDataProvider(db_path=...)` 傳遞；`db_path=None` 時跳過 DB 層，直接呼叫 API（降級不 raise）。
3. 原因：
   - FinMind 提供台股完整財務資料且免費可用；MOPS 原始頁面爬取結構不穩定，FinMind 結構化 JSON 更可靠。
   - 財務資料變動頻率低（季度/年度），15 天 TTL 在保持資料足夠新鮮與減少 API 呼叫之間取得平衡。
   - SWR 策略避免阻塞：陳舊資料立即可用，背景刷新不影響 scan-market 回應時間。
   - 背景執行緒去重（`_refreshing` set）確保同一 dataset 最多一個刷新執行緒在飛，不重複呼叫 API。
4. 影響：
   - 新增 `financial_data_cache` SQLite 資料表（`schema.py`、`EDD §9.3/§16`）。
   - 新增 `FinMindFinancialDataProvider`（`stock_monitor/adapters/financial_data_finmind.py`）。
   - 四項已知解析約束需在實作中特別處理（EDD §9.3 關鍵實作約束）：
     - ROC 年份：使用 `date` 欄位而非 `year` 欄位
     - EPS 加總：同年所有季度加總後再取年均
     - Liabilities 型別名稱：FinMind 使用 `Liabilities`（非 `TotalLiabilities`）
     - NT$ 單位正規化：資產負債表值需除以 1,000 轉換為 NT$ 千元單位
   - `API_CONTRACT.md` 同步新增 `FinancialDataPort` 介面定義。
   - CLAUDE.md §7 symbol contract 新增三個條目。

## ADR-018 財務資料三源平行執行（ParallelFinancialDataProvider）
1. 狀態：Accepted
2. 決策：
   - 財務估值資料（股利、EPS 等）由三個來源（FinMind P1、MOPS/TWSE P2、Goodinfo P3）**同時平行執行**，而非序列備援（P1 失敗才試 P2）。
   - 以 `ThreadPoolExecutor(max_workers=3)` 同時觸發三個 `SWRCacheBase` 子類別；60 秒後逾時，取已完成者。
   - 三源完成後比較各自快取的 `fetched_at`，取最新者回傳給估值方法。
   - 三源均不可用（`ProviderUnavailableError`）時，估值方法記錄 `SKIP_INSUFFICIENT_DATA`，不中斷掃描。
   - 各源維護獨立的 `financial_data_cache` 記錄（`provider` 欄位區分 `'finmind'`/`'mops'`/`'goodinfo'`），互不覆蓋。
   - 介面入口為 `ParallelFinancialDataProvider`（`stock_monitor.adapters.financial_data_fallback`），取代原 `FallbackFinancialDataProvider`（序列備援）。
3. 原因：
   - 序列備援下，P1 失敗（如 FinMind 達到速率上限）要等到下次才試 P2，延誤一整個估值週期。平行執行在同一次呼叫中即可取得任一來源的最新資料。
   - 各源有獨立 SWR 快取，一個來源暫時失效時，其他來源的 stale cache 仍可提供合理資料，減少 `SKIP_INSUFFICIENT_DATA` 頻率。
   - `fetched_at` 比較策略確保回傳最新鮮的資料，三源同時有效時不做隨機選擇。
   - SWR 語意統一：cache miss → 同步取資料，cache stale → 背景刷新。兩者不可對調（CR-FIN-04）。
4. 影響：
   - `financial_data_cache` schema 新增 `provider TEXT NOT NULL` 欄位，PRIMARY KEY 改為 `(provider, stock_no, dataset)`（EDD §16）。
   - 舊表由 `SWRCacheBase._migrate_cache_table()` 自動升級（ADD COLUMN + recreate index，非破壞性）。
   - `market_scan_methods.py` 的 `load_enabled_scan_methods` 改注入 `ParallelFinancialDataProvider`。
   - `API_CONTRACT.md` §5.10 更新為 `ParallelFinancialDataProvider` 並補充平行執行契約。
   - CLAUDE.md §5.7/§6/§7/§12 新增 FR-21 規則、schema 升級說明、symbol contract、CR-FIN-01~05 禁止清單。
