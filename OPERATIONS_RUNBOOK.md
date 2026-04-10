# OPERATIONS_RUNBOOK - Stock Monitoring System

版本：v0.1  
日期：2026-04-10  
來源基準：`PDD_Stock_Monitoring_System.md`、`EDD_Stock_Monitoring_System.md`

## 1. 文件目的
定義上線、日常巡檢、故障排查與補償操作流程，確保系統行為與規格一致。

## 2. 啟動前檢查清單
1. `.env` 必填：
   - `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TO_GROUP_ID`（或 alias）
   - `DB_PATH`
   - `MAX_RETRY_COUNT=3`
   - `STALE_THRESHOLD_SEC=90`
2. SQLite 檢查：
   - `PRAGMA foreign_keys=ON`
   - JSON1 可用（`SELECT json_valid('[]')`）
3. 檔案路徑：
   - DB 目錄可寫
   - `logs/` 可寫
4. 時區：
   - `APP_TZ=Asia/Taipei`

## 3. 日常排程預期
1. 每 60 秒執行盤中輪詢。
2. 非交易時段不得發通知。
3. 每交易日 14:00 執行估值結算一次。
4. 每分鐘最多 1 封 LINE 訊息。

## 4. 巡檢指標
1. `line_send_success_rate`（近 1 小時）
2. `cooldown_block_count`（近 1 小時）
3. `pending_delivery_count`（應趨近 0）
4. `market_timeout_count`、`stale_quote_count`、`data_conflict_count`
5. `daily_valuation_success`（交易日 14:00 後）

## 5. 常見異常與處置
### 5.1 啟動 fail-fast（設定錯誤）
1. 症狀：啟動即退出，錯誤指向 LINE 參數。
2. 處置：
   - 修正 `.env` 的 canonical 或 alias 參數。
   - 重新啟動並確認 health check 為 `ok`。

### 5.2 行情 timeout / provider 不可用
1. 症狀：`MARKET_TIMEOUT` WARN 持續出現。
2. 處置：
   - 確認網路與資料源可達性。
   - 檢查重試次數是否達上限。
   - 若長時間不可用，暫停通知並公告觀測狀態。

### 5.3 LINE 發送失敗
1. 症狀：`LINE_SEND_FAILED` ERROR，`message` 無新增。
2. 處置：
   - 檢查 token/groupId 是否仍有效。
   - 驗證 LINE API 配額與群組權限。
   - 恢復後觀察下一分鐘是否自動恢復。

### 5.4 LINE 成功但 DB 失敗
1. 症狀：`DB_WRITE_FAILED_AFTER_SEND`，`pending_delivery_ledger` 有 PENDING。
2. 處置：
   - 優先確認 DB 可寫。
   - 執行補償 worker。
   - 確認 ledger 轉為 `RECONCILED` 且無重複發送。

## 6. 補償操作流程
1. 查詢 `pending_delivery_ledger` 中 `PENDING` 項。
2. 逐筆回補 `message` 表（同分鐘批次回補）。
3. 成功後標記 `RECONCILED`。
4. 失敗時遞增 `retry_count`，必要時標記 `FAILED` 並人工介入。
5. 補償期間該分鐘事件仍視同已通知，不可重送。

## 7. 交易時段判斷例外處理
1. 08:45 後開始檢查大盤新資料。
2. 09:00 後仍無當日大盤新資料視為不開市。
3. 13:30 後一律不進行盤中輪詢通知。

## 8. 發布後驗證（Smoke）
1. 以測試股票觸發一筆 `status=1`，確認 60 秒內可收到 LINE。
2. 5 分鐘內重複觸發同鍵，確認被冷卻擋下。
3. 模擬 DB 失敗，確認補償佇列生成。
4. 啟動重啟後，確認同分鐘不重送。

## 9. 變更管理（Spec-Driven）
1. 任何營運流程改動，先更新 `PDD/EDD`。
2. 同步更新 `.feature` 與 `TEST_PLAN`。
3. 測試綠燈後才允許上線。
