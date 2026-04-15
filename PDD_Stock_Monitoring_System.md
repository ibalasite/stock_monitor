# PDD - 台股價格監控與 LINE 通知系統（V0/V1）

版本：v1.0  
日期：2026-04-14  
狀態：Draft（可進入 review）

## 1. 文件目的
定義一套「台股價格監控」產品需求，讓工程與企劃可共同對齊：
- 盤中即時監控指定股票價格
- 價格達條件即發送 LINE 通知
- 每日收盤後計算合理價/便宜價，供隔日監控

## 2. 背景與問題
- 使用者目前需手動盯盤，容易錯過理想買點。
- 市場波動快，若沒有自動化通知，決策延遲成本高。
- 同訊號重複通知過多會造成疲勞，反而忽略真正重要訊號。

## 3. 產品目標
### 3.1 業務目標
- 降低手動盯盤時間。
- 在價格進入可行動區間時即時提醒。

### 3.2 成功指標（初版）
- 通知延遲：觸發後 60 秒內送達（P95）。
- 通知準確率：> 99%（非資料源中斷情況）。
- 重複通知控制：相同訊號 5 分鐘內不重複發送。

## 4. 目標使用者
- 個人投資者（優先）。
- 後續可擴展至小型投資團隊（群組通知）。

## 5. 產品範圍
### 5.1 In Scope（V0）
- 維護監控清單（股票代碼、手動合理價、手動便宜價、啟用狀態）。
- 盤中每分鐘檢查最新價格。
- 價格低於合理價或便宜價時發 LINE 訊息。
- 5 分鐘通知冷卻機制。
- 支援 LINE Bot 發送到指定群組。
- 本機 SQLite 儲存監控、通知與系統日誌。
- 啟動前驗證 LINE 必要設定（token/groupId），錯誤時 fail-fast。
- 服務重啟後保持通知去重一致性（避免同分鐘重複推送）。

### 5.2 In Scope（V1）
- 每交易日 14:00 執行一次估值計算。
- 支援多種估值方法（可 enable/disable）。
- 每方法各自產生合理價/便宜價並寫入 SQLite。
- 第一批估值方法固定為三類：
  - 艾蜜莉紅綠燈複合法（`emily_composite_v1`）
  - 股海老牛股利殖利率法（`oldbull_dividend_yield_v1`）
  - 雷司紀四法混合法（`raysky_blended_margin_v1`）
- 盤中若符合任一方法門檻即通知，訊息需含方法名稱。
- 同一分鐘多股票/多方法命中時，整併為單一彙總訊息發送。
- 每交易日開盤起始（第一個可交易分鐘）先發 1 封「開盤監控設定摘要」LINE：
  - 列出當日監控股票清單。
  - 列出啟用中的判斷方法（手動 + 啟用估值方法）。
  - 逐股票逐方法列出 `fair_price/cheap_price`（手動值與估值快照值）。

### 5.3 Out of Scope（目前不做）
- 自動下單。
- 多市場（美股/加密）同時監控。
- 分散式高可用叢集部署。

## 6. 使用流程（User Flow）
1. 使用者設定監控股票與手動合理價/便宜價。
2. 交易日開盤起始先推播 1 封「當日監控設定摘要」（股票、方法、合理價、便宜價）。
3. 系統在交易時段每 1 分鐘抓價一次。
4. 若現價低於合理價或便宜價，立即推播 LINE 到指定群組。
5. 5 分鐘內同股票同狀態不再推播，且不更新訊息時間。
6. 每交易日 14:00 執行估值計算並入庫。
7. 隔日盤中依各估值方法門檻持續監控並通知。

## 7. 功能需求
### FR-01 監控清單管理
- 可新增/停用/刪除目標股票。
- 每筆需包含：`stock_no`、`manual_fair_price`、`manual_cheap_price`、`enabled`。

### FR-02 盤中價格輪詢
- 排程每 60 秒執行。
- 僅在交易時段執行。
- 交易日判斷採簡易規則：
  - 週六/週日不交易。
  - 參考台灣政府行事曆（內政部/行政院人事行政總處）假日資料。
  - 若資料源顯示當日大盤無更新資料，視為不開盤。
- 失敗需重試並記錄錯誤。
- 重試上限預設為 `MAX_RETRY_COUNT=3`（可配置）。
- 若大盤資料來源逾時或不可用，該分鐘跳過通知流程並記錄 WARN/ERROR 日誌。
- 行情資料需滿足新鮮度門檻（預設 90 秒內），逾時資料視為 stale，該分鐘跳過通知。
- 若啟用多資料來源且報價差異超過門檻，標記 `DATA_CONFLICT`，該分鐘跳過通知並記錄 WARN。
- 被跳過分鐘不得補發過期訊號。
- 行情資料採雙來源：TWSE MIS（主）與 Yahoo Finance TW HTML scraping（副），以報價時間戳（`tick_at`）較新者為準。
  - 若 TWSE `a` 欄位（委賣一）在此輪詢快照為空或 `'-'`，先嘗試 TWSE 內部 `_price_cache`（本次 daemon 生命週期的最後已知委賣一）。
  - 若 TWSE cache 有值但 Yahoo 的 `regularMarketTime` 較新，採 Yahoo 的價格與時間。
  - 若 TWSE cache 為空（冷啟動第一輪），直接使用 Yahoo 的值。
  - 若兩者均不可用，該股票該分鐘標記 `STALE_QUOTE`，跳過通知。

### FR-03 訊號判斷
- `stock_status=1`：`market_price <= fair_price`（低於合理價）
- `stock_status=2`：`market_price <= cheap_price`（低於便宜價）
- 若同時符合 `1` 與 `2`，僅發送 `2`（便宜價優先）。
- Phase 1 優先使用手動價格（例如：2330 fair=1500, cheap=1000）。

> **編號說明**：FR-04 此編號預留，目前未使用。FR-15～18 為後期加入功能，依制定日期緊接 FR-03 排列；邏輯順序請參閱 EDD §3.3／§7.6／§9.x。

### FR-15 雙行情來源（TWSE 主 + Yahoo Finance 副）
- 盤中行情採雙來源抓取：
  - **主來源**：TWSE MIS `getStockInfo.jsp`（`a` 欄位第一筆，委賣一，即最佳委賣價）。  
    - TWSE 在委買委賣訂單薄短暫消失時 `a` 為空或 `-`；系統以 `_price_cache`（daemon 生命週期內最後已知委賣一）補全。
    - 冷啟動 cache 為空時，以 `y`（昨日收盤）種子填充。
  - **副來源**：Yahoo Finance TW quote 頁面 HTML scraping（`tw.stock.yahoo.com/quote/{stock_no}`）。  
    - 從 server-render HTML 的委賣價區塊解析**委賣一**（最佳委賣價），作為 `price`。  
    - 若 委賣一欄位不存在（盤後或休市），fallback 使用 `regularMarketPrice`。  
    - URL 格式：`stock_no` only，不需 `.TW`/`.TWO` suffix（TSE/OTC 均可）。  
    - 採用 HTML scraping（近即時，秒級延遲），不使用 v8 chart API（後者有 ~20 分鐘延遲）。
- **取捨規則（Freshness-First）**：
  1. 若 TWSE cache 有值且 Yahoo 的 `regularMarketTime` 不比 TWSE cache 的 `tick_at` 新 → 採 TWSE cache 值。
  2. 若 Yahoo 的 `regularMarketTime` 嚴格大於 TWSE cache 的 `tick_at` → 採 Yahoo 值（包含 Yahoo 的 `regularMarketTime` 作為 `tick_at`）。
  3. 若 TWSE cache 為空（冷啟動第一個輪詢 TWSE `a='-'` 且 cache 為空）→ 直接採 Yahoo 值。
  4. 若兩者均無法取得有效價格 → 該股票該分鐘標記 `STALE_QUOTE`，跳過通知。
- Yahoo Finance 頁面請求失敗（逾時、HTTP 錯誤）不得中斷主流程：記錄 WARN，回退使用 TWSE cache。
- TWSE `ex` 欄位快取（`tse/otc`）需由 `TwseRealtimeMarketDataProvider` 在每輪詢更新；Yahoo adapter 接受 exchange_map dict 作為輸入（interface 相容性，不用於 URL 建構）。
- **採用委賣一而非成交價（`z`）的原因**：委賣一代表當下可立即買到的最低價格（明確且即時），成交價 `z` 在兩筆成交之間顯示為 `'-'`（短暫閃爍），委賣一維持連續更新，更能反映現況。

### FR-16 行情 adapter 可獨立替換
- `MarketDataPort` 定義 `get_realtime_quotes(stock_nos) -> dict[str, dict]` 與 `get_market_snapshot(now_epoch) -> dict`。
- `CompositeMarketDataProvider` 實作 Freshness-First 合併邏輯，不直接依賴 TWSE 或 Yahoo 的具體實作細節；只依賴 `MarketDataPort` 介面（可注入任何 provider）。
- 未來可在不改 Application layer 的情況下替換任一 provider。
- 使用者以自己的 LINE Official Account / Messaging API Bot 發送。
- 預設發送至使用者指定群組（groupId）。
- 每分鐘最多發送 1 封彙總訊息（不並發發送）。
- 訊息內容需含：股票代碼、觸發狀態、方法名稱（可多個）、觸發價、現價、時間。
- LINE 通知文字格式必須由模板驅動（Template-driven），不得在程式中硬編碼完整訊息文案。
- 通知延遲目標：觸發後送達 P95 <= 60 秒。

### FR-17 文字檔模板載入（File-based Templates）

**目標**：企劃 / 文案人員**不需碰 Python 程式碼**，直接以記事本修改 `.j2` 純文字檔即可變更 LINE 推播的用字遣詞（wording）。改動 wording 完全不需工程人員介入，也不需 Code Review。

**業務規格**：

- LINE 推播文案以獨立 `.j2` 純文字檔管理，存放於 `templates/line/` 目錄。
- 用記事本（Notepad 等文字編輯器）可直接開啟與修改，無需了解 Python。
- 使用 `{{ 變數名 }}` 插入資料（如股票名稱、價格）；使用 `{% for %}...{% endfor %}` 迴圈列出多筆股票。
- 若 `.j2` 檔遺失，系統自動沿用內建預設格式並記錄警告；不會靜默失敗，不影響正常通知。
- 程式呼叫介面保持不變；工程實作細節（Jinja2 FileSystemLoader、路徑安全等）見 EDD §7。


### FR-18 每日估值時儲存股票中文名稱（Stock Name Persistence）

**目標**：股票中文名稱在每交易日 14:00 估值時一併寫入 SQLite，盤中分析與通知全程從 DB 取名稱，不在即時報價輪詢中額外抓取。

**業務規格**：

- `watchlist` 資料表新增 `stock_name TEXT NOT NULL DEFAULT ''` 欄位。
- 每交易日 14:00 執行估值時，同步向行情來源取得各股票中文名稱，並以 UPDATE 寫入 `watchlist.stock_name`。
- 盤中每分鐘監控循環（`run_minute_cycle`）的顯示名稱（`stock_name_map`）一律由 `watchlist.stock_name` 提供，不再從即時報價的 `name` 欄位取得。
- 開盤摘要、觸發通知的股票顯示（如 `台積電(2330)`）均使用 DB 名稱。
- 若 `watchlist.stock_name` 為空字串，顯示時 fallback 為股票代碼（如 `2330`），行為與現行一致。
- 名稱不須每分鐘更新；無需額外的 API 呼叫頻率。

### FR-05 通知冷卻（Notification Cooldown）
- 維度：`stock_no + stock_status`。
- 規則：若最後一次通知 `update_time` 在 5 分鐘內，則不發送、也不更新該筆時間。

### FR-06 訊息表（message table）
- 欄位需包含：`stock_no`、`message`、`stock_status`、`update_time`。
- 可擴充欄位：`methods_hit`、`minute_bucket`，供彙總訊息追蹤。
- 每次成功發送通知後寫入一筆，作為冷卻判斷依據。
- 需支援同分鐘去重（`stock_no + minute_bucket` 唯一）。
- 每分鐘彙總訊息發送成功後，該分鐘所有 message 寫入需一致提交（transaction）。
- 發送失敗時僅寫系統 log，不寫入 `message`。
- 若 LINE 發送成功但 `message` 落盤失敗，需寫入補償佇列並重試回補；補償完成前視同已通知，避免重複推送。
- 去重需有 `idempotency_key`（由 `stock_no + minute_bucket` 組成，**不含** `stock_status`），確保重啟後不重複通知（對齊 EDD §2.4）。

### FR-07 每日估值結算
- 每交易日 14:00 固定執行一次。
- 計算失敗時不得覆蓋舊資料（不 update）。
- 寫入合理價、便宜價、方法名稱、方法版本、交易日。
- 估值快照唯一鍵應包含方法版本（`stock_no + method_name + method_version + trade_date`）。

### FR-08 多方法估值策略（1 對多）
- 估值方法採全域 enable/disable（方法本身是否參與計算）。
- 同一方法名稱同時間僅允許一個啟用版本（避免多版本同時生效）。
- 監控股票清單由 `watchlist` 管理。
- 估值結果按 `stock_no + method_name + method_version + trade_date` 寫入快照。
- 同一股票若多方法命中，於同分鐘彙總訊息中附上方法清單。

### FR-09 啟動前設定驗證（LINE）
- 啟動前必檢 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LINE_TO_GROUP_ID`。
- 為向後相容，可接受別名 `CHANNEL_ACCESS_TOKEN` / `TARGET_GROUP_ID`；若規範名與別名同時存在，優先使用規範名。
- token 無效或 groupId 無效時，服務應 fail-fast 並提供可操作錯誤訊息。
- secret 不得輸出於明文日誌。

### FR-10 重啟恢復與一致性
- 服務啟動後需先載入最近通知狀態，再進入輪詢。
- 若存在補償中的通知，啟動後需續跑補償流程，不得重複推送同分鐘訊號。

### FR-11 第一批估值方法定義（三方法）
- `emily_composite_v1`（艾蜜莉）
  - 子法：股利法、歷年股價法、本益比法、股價淨值比法。
  - 各子法先算 `cheap/fair`，再對可用子法取平均；最後乘安全邊際係數（預設 `0.9`）。
  - 輸出：`fair_price`（合理價）、`cheap_price`（便宜價）。
- `oldbull_dividend_yield_v1`（股海老牛）
  - 使用「平均股利 + 目標殖利率」反推價格。
  - `fair_price = avg_dividend / 0.05`（5% 殖利率）
  - `cheap_price = avg_dividend / 0.06`（6% 殖利率）
- `raysky_blended_margin_v1`（雷司紀）
  - 子法：PE、股利、PB、NCAV。
  - 對可用子法先算每法 `fair/cheap`，再做中位數或加權融合（權重可配置）。
  - 需套安全邊際係數（預設 `0.9`）產生 `cheap_price`。

### FR-12 估值資料來源充分性與每日可計算規範
- 系統需定義「每方法最小輸入資料集」，並每日 14:00 檢查可計算性。
- 來源必須以公開授權資料為主，且至少具備主來源 + 備援來源。
- 每股票每方法結果分三種：
  - `SUCCESS`：資料完整，寫入當日快照。
  - `SKIP_INSUFFICIENT_DATA`：資料不足，不覆蓋舊快照，寫 WARN。
  - `SKIP_PROVIDER_ERROR`：來源失敗，不覆蓋舊快照，寫 ERROR。
- 不允許因單一方法失敗而阻斷整體估值任務；其餘方法需持續執行。

### FR-13 開盤監控設定摘要通知（新增）
- 觸發時機：每交易日「第一個可交易分鐘」僅發送一次（例如 09:00 或當日首個判定為開市的分鐘）。
- 通知目的：在盤中訊號開始前，先讓使用者確認「今天系統正在監控什麼」。
- 訊息內容至少包含：
  - 逐股票逐方法的 `fair_price` 與 `cheap_price`。
  - 股票識別格式需支援 `中文名(代號)`（例如：`台積電(2330)`）。
- 價格來源規則：
  - `manual_rule`：讀取 `watchlist.manual_fair_price/manual_cheap_price`。
  - 估值方法：讀取該股票「<= 當日」最新 `valuation_snapshots`。
- 缺值處理：
  - 若某股票某方法尚無可用快照，摘要中仍需列出該方法並標示 `N/A`。
- 去重規則：
  - 同一交易日僅可發送一封摘要通知；服務重啟後不得重複補發。

### FR-14 LINE 訊息模板化（Template-driven Rendering）
- 適用範圍：**所有對外發送至 LINE 的訊息都必須模板化**，包含但不限於：
  - 每分鐘彙總通知（minute digest）
  - 開盤監控設定摘要通知（opening summary）
  - 單股觸發內容列（status=1/2）
  - 測試推播 / 營運驗證推播（若系統提供）
- 規則：
  - 訊息格式與文案必須由模板檔（或模板儲存層）渲染，不可把完整文案寫死在業務程式碼中。
  - 模板需支援變數插值（例如：`stock_display`、`method_label`、`fair_price`、`cheap_price`）。
  - 模板需可在不改主程式碼的前提下調整文案與排列方式。
  - 業務層只能傳遞 `template_key + context`，不得直接拼接最終 LINE 文案。
  - 模板缺失或渲染失敗需有明確錯誤日誌，且不得默默改用未知格式。
- 行動端可讀性要求（初版）：
  - 開盤摘要應支援手機友善精簡格式，例如：`台積電(2330) 手動 2000/1500`。

## 8. 非功能需求（NFR）
- 時區：Asia/Taipei。
- 穩定性：資料源短暫異常不應造成服務終止。
- 可追蹤性：所有通知與錯誤有 DB 日誌。
- 可維護性：估值模型需可插拔（版本化）。
- 架構：需符合 Clean Architecture（Domain/Application/Infrastructure 分層）。

## 9. 外部服務與資料來源策略
### 9.1 LINE
- 使用 `LINE Messaging API`（LINE Notify 已終止）。
- 需建立 Official Account 與 Channel Access Token。

### 9.2 台股行情（雙來源架構）
- 以公開可取得網頁/API 為主（個人使用）。
- 主來源：TWSE MIS `getStockInfo.jsp`（`a` 欄位委賣一，`tlong` 毫秒時間戳）。
- 副來源：Yahoo Finance TW HTML scraping（`tw.stock.yahoo.com/quote/{stock_no}`，不使用 `.TW`/`.TWO` suffix）。
  - 優點：Yahoo 保留近即時委賣一，適合 TWSE `a` 欄位為空的 daemon 冷啟動暖機。
  - 限制：Yahoo HTML scraping 依賴頁面版型穩定；故以時間戳較新者為準（Freshness-First）。
- 需保留 provider 抽換能力，後續可替換成其他 API。
- 可評估券商 API 作備援或升級。

### 9.3 估值資料來源（每日可計算）
| 資料類型 | 主來源 | 備援來源 | 用途 |
|---|---|---|---|
| 盤中/收盤價格、歷史價格 | TWSE / TPEx 公開行情 | Yahoo Finance 台股代碼 | 歷年股價法、PE/PB 區間、監控觸發 |
| 股利（年/季） | 公開資訊觀測站（MOPS） | TWSE 公開彙整資料 | 股利法、殖利率法 |
| EPS（近一年、長期） | MOPS 財報 | TWSE 財報彙整頁 | PE 法 |
| 每股淨值 / 股東權益 | MOPS 財報 | TWSE 財報彙整頁 | PB 法 |
| 流動資產、總負債、股數 | MOPS 資產負債表/基本資料 | TWSE 公開欄位 | NCAV 法 |

資料充分性原則：
- 若當日無新財報，允許沿用最近一期有效財報值（有時間戳）。
- 需在估值結果中保留 `input_asof_date`，避免誤判資料新鮮度。

## 10. 資料結構（摘要）
- `watchlist`
- `valuation_snapshots`
- `message`（`stock_no`, `message`, `stock_status`, `update_time`）
- `pending_delivery_ledger`（補償佇列）
- `system_logs`

## 11. Clean Architecture 建議設計
### Domain Layer
- Entities：`Stock`, `PriceSignal`, `ValuationResult`, `MessageRecord`
- Value Objects：`StockStatus`, `Money`, `TradeDateTime`
- Domain Services：`SignalPolicy`, `CooldownPolicy`

### Application Layer
- Use Cases：
  - `CheckIntradayPriceUseCase`
  - `SendSignalMessageUseCase`
  - `RunDailyValuationUseCase`
- Ports：
  - `MarketDataPort`
  - `HolidayCalendarPort`
  - `ValuationMethodPort`
  - `LineMessagingPort`
  - `MessageRepositoryPort`
  - `ValuationRepositoryPort`

### Infrastructure Layer
- Adapters：
  - `TwseRealtimeMarketDataProvider`（主行情，含 `_price_cache` 與 `ex` 欄位記憶）
  - `YahooFinanceMarketDataProvider`（副行情，Yahoo Finance TW HTML scraping）
  - `CompositeMarketDataProvider`（Freshness-First 聚合，依 `tick_at` 取較新值）
  - `TaiwanHolidayCalendarAdapter`
  - `LineMessagingApiAdapter`
  - `SqliteMessageRepository`
  - `SqliteValuationRepository`
- Scheduler：`every 60s` 與 `14:00 daily`

### Interface Layer
- CLI/Config 管理、Health Check、日誌輸出。

## 12. 驗收標準（UAT）
1. 設定 `2330 fair=1500, cheap=1000`，當價格 <= 1500 或 <= 1000 時，LINE 在 60 秒內收到群組通知。
2. 相同 `2330 + stock_status` 5 分鐘內不重複推送，且 `message.update_time` 不更新。
3. `message` 表可查到 `stock_no/message/stock_status/update_time`。
4. 非交易時段（含週末與假日）不進行盤中輪詢。
5. 每交易日 14:00 估值任務執行一次；失敗不覆蓋舊估值。
6. 同一分鐘若多股票/多方法同時觸發，系統只發送 1 封彙總訊息，內含所有命中方法資訊。
7. 若同一股票同分鐘同時符合 `status=1` 與 `status=2`，僅以 `status=2` 呈現與通知。
8. 若發生「LINE 成功、DB 落盤失敗」，系統可在補償流程完成回補，且補償期間不重複發送同分鐘訊息。
9. 若缺少或誤設 `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_TO_GROUP_ID`（或其別名），系統啟動 fail-fast 並輸出明確錯誤原因。
10. 服務重啟後，已送出的同分鐘訊號不得再次發送；補償中訊號可續跑回補。
11. 若行情資料 stale 或來源衝突，該分鐘不得通知並需有 `STALE_QUOTE` 或 `DATA_CONFLICT` 的 WARN 日誌。
12. 每交易日 14:00 應對每檔股票嘗試執行三個方法（`emily_composite_v1`、`oldbull_dividend_yield_v1`、`raysky_blended_margin_v1`）；資料不足方法需 `skip + log` 且不得覆蓋舊快照。
13. 每交易日開盤第一個可交易分鐘，系統需先發送 1 封「監控設定摘要」至 LINE，內容含股票、方法、各方法 `fair/cheap`，且同一交易日不得重複發送；該摘要需由模板渲染（非程式硬編碼）。
14. 所有發送到 LINE 的訊息（彙總、摘要、觸發列、測試推播）皆須透過 `template_key + context` 渲染；程式碼中不得直接硬編碼最終文案。
15. 盤中行情採雙來源（TWSE 主 + Yahoo Finance 副）：以 `tick_at` 較新者為準（Freshness-First）；Yahoo Finance 呼叫失敗不得中斷主流程；兩者均無法取得時該分鐘 `STALE_QUOTE`。

## 13. 風險與因應
- 資料源中斷：加重試、fallback、錯誤告警。
- 通知與落盤不一致：補償佇列 + 回補重試，避免重複告警與狀態遺失。
- 行事曆資料延遲：以「無新大盤資料」作次級判斷避免誤開盤。
- 設定錯誤導致無法推播：啟動前驗證 + fail-fast。

## 14. 里程碑
1. M1（2-3 天）：V0 可用，manual fair/cheap 監控 + LINE 通知 + cooldown。
2. M2（2-4 天）：V1 估值快照入庫 + fair/cheap 監控。
3. M3（1-2 天）：補齊監控報表與操作文件。

## 15. 待決策事項
- 三方法融合權重（`raysky_blended_margin_v1`）預設值是否採等權重。
- 多來源行情衝突時的優先權規則。✅ 已定版：Freshness-First（`tick_at` 較新者勝出）
- NCAV 子法於金融股是否預設停用。

## 16. 參考來源
- TWSE Q&A（MIS）  
  https://www.twse.com.tw/en/products/information/qa.html
- TWSE 即時資訊服務  
  https://www.twse.com.tw/en/products/information/real-time.html
- TPEx 即時資料產品  
  https://www.tpex.org.tw/en-us/service/data/product/real-time.html
- LINE Developers 2024 News（含 LINE Notify 終止訊息）  
  https://developers.line.biz/en/news/2024/
- LINE Messaging API Getting Started  
  https://developers.line.biz/en/docs/messaging-api/getting-started/
- Channel Access Token  
  https://developers.line.biz/en/docs/basics/channel-access-token/
- 公開資訊觀測站（MOPS）  
  https://mops.twse.com.tw/mops/web/index
- 艾蜜莉紅綠燈四法（公開文章）  
  https://cmnews.com.tw/article/emily-3c27e56c-d95c-11ef-b371-f00f7406f718
- 股海老牛股利估價（公開文章）  
  https://www.mirrormedia.mg/story/20240108money004
- 雷司紀四法與安全邊際（公開文章）  
  https://www.rayskyinvest.com/16980/value-investing-pe-pb-ncav-dividend
