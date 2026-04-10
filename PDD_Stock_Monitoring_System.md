# PDD - 台股價格監控與 LINE 通知系統（V0/V1）

版本：v0.7  
日期：2026-04-10  
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
- 盤中若符合任一方法門檻即通知，訊息需含方法名稱。
- 同一分鐘多股票/多方法命中時，整併為單一彙總訊息發送。

### 5.3 Out of Scope（目前不做）
- 自動下單。
- 多市場（美股/加密）同時監控。
- 分散式高可用叢集部署。

## 6. 使用流程（User Flow）
1. 使用者設定監控股票與手動合理價/便宜價。
2. 系統在交易時段每 1 分鐘抓價一次。
3. 若現價低於合理價或便宜價，立即推播 LINE 到指定群組。
4. 5 分鐘內同股票同狀態不再推播，且不更新訊息時間。
5. 每交易日 14:00 執行估值計算並入庫。
6. 隔日盤中依各估值方法門檻持續監控並通知。

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

### FR-03 訊號判斷
- `stock_status=1`：`market_price <= fair_price`（低於合理價）
- `stock_status=2`：`market_price <= cheap_price`（低於便宜價）
- 若同時符合 `1` 與 `2`，僅發送 `2`（便宜價優先）。
- Phase 1 優先使用手動價格（例如：2330 fair=1500, cheap=1000）。

### FR-04 LINE 通知
- 使用者以自己的 LINE Official Account / Messaging API Bot 發送。
- 預設發送至使用者指定群組（groupId）。
- 每分鐘最多發送 1 封彙總訊息（不並發發送）。
- 訊息內容需含：股票代碼、觸發狀態、方法名稱（可多個）、觸發價、現價、時間。
- 通知延遲目標：觸發後送達 P95 <= 60 秒。

### FR-05 通知冷卻
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

### 9.2 台股行情
- 以公開可取得網頁/API 為主（個人使用）。
- 需保留 provider 抽換能力，後續可替換成其他 API。
- 可評估券商 API 作備援或升級。

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
  - `TwsePublicDataAdapter`
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
- Phase 2 估值方法第一批清單與權重策略。
- 多來源行情衝突時的優先權規則。

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
