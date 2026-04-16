# OPERATIONS_RUNBOOK - Stock Monitoring System

版本：v0.4  
日期：2026-04-17  
來源基準：`PDD_Stock_Monitoring_System.md`（v1.1）、`EDD_Stock_Monitoring_System.md`（v1.1）

## 1. 文件目的
定義上線、日常巡檢、故障排查與補償操作流程，確保系統行為與規格一致。

## 2. 啟動前檢查清單
1. 以系統環境變數啟動（不強制 `.env`）：
   - `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TO_GROUP_ID`（或 alias: `CHANNEL_ACCESS_TOKEN`、`TARGET_GROUP_ID`）
   - `LINE_TEMPLATE_DIR`（模板目錄）
   - `LINE_TEMPLATE_MINUTE_DIGEST`、`LINE_TEMPLATE_OPENING_SUMMARY`
   - `LINE_TEMPLATE_TRIGGER_ROW`、`LINE_TEMPLATE_TEST_PUSH`（若提供 test push）
   - `APP_TIMEZONE=Asia/Taipei`（未設定時預設 `Asia/Taipei`；若設定嘗無效時區名稱，服務啟動時將 fail-fast 並丟出 `ValueError`，不得靜默 fallback，見 EDD §13.1 CR-SEC-03）
   - `MAX_RETRY_COUNT=3`
   - `STALE_THRESHOLD_SEC=90`
   - `COOLDOWN_SEC=300`
2. 啟動命令需帶 DB 路徑（或採預設 `data/stock_monitor.db`）：
   - `python -m stock_monitor --db-path data/stock_monitor.db init-db`
   - `python -m stock_monitor --db-path data/stock_monitor.db run-once`
   - `python -m stock_monitor --db-path data/stock_monitor.db run-daemon --poll-interval-sec 60 --valuation-time 14:00`
3. 監控清單：
   - `watchlist` 至少需有 1 檔 `enabled=1`，否則 `run-once` 會 `empty_watchlist` 而跳過
4. SQLite 檢查：
   - `PRAGMA foreign_keys=ON`
   - JSON1 可用（`SELECT json_valid('[]')`）
5. 檔案路徑：
   - DB 目錄可寫
   - `logs/` 可寫

## 3. 日常排程預期
1. 每 60 秒執行盤中輪詢。
2. 非交易時段不得發通知。
3. 每交易日 14:00 執行估值結算一次。
4. 每分鐘最多 1 封 LINE 訊息。
5. 所有出站 LINE 訊息需走模板渲染（彙總/摘要/觸發列/測試推播），不得硬編碼最終文案。

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
   - 修正系統環境變數（canonical 或 alias 參數）。
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
4. 開盤摘要（Opening Summary）冪等狀態以 DB 記錄為準；
   若 `daemon` 在 09:01 後重啟，當日尚未發送開盤摘要時應正常補送（見 EDD §13.3 CR-ARCH-06、CR-CODE-06）。

## 8. 發布後驗證（Smoke）
1. 以測試股票觸發一筆 `status=1`，確認 60 秒內可收到 LINE。
2. 5 分鐘內重複觸發同鍵，確認被冷卻擋下。
3. 模擬 DB 失敗，確認補償佇列生成。
4. 啟動重啟後，確認同分鐘不重送。

## 9. 變更管理（Spec-Driven）
1. 任何營運流程改動，先更新 `PDD/EDD`。
2. 同步更新 `.feature` 與 `TEST_PLAN`。
3. 測試綠燈後才允許上線。

## 10. scan-market CLI 操作指南（FR-19）
### 10.1 指令格式
```bash
python -m stock_monitor \
  --db-path data/stock_monitor.db \
  scan-market \
  [--output-dir ./output]
```

### 10.2 執行前提
1. DB 已初始化（`init-db` 已執行）。
2. `valuation_methods` 表中 3 個估值方法均有 `enabled=1` 記錄。
3. 網路可連到 TWSE 與 TPEX 公開 API。
4. `--output-dir` 目錄可寫（預設為當前目錄）。

### 10.3 輸出說明
| 檔案 | 說明 |
|---|---|
| `scan_results_above_cheap.csv` | 高於便宜價但低於合理價的股票 |
| `scan_results_uncalculable.csv` | 全方法無法計算（含原因） |
| watchlist（DB） | 低於便宜價股票自動 upsert |

CSV 欄位：`stock_no`, `stock_name`, `agg_fair_price`, `agg_cheap_price`, `yesterday_close`, `methods_computed`, `methods_skipped`, `skip_reasons`。

### 10.4 異常排查
1. `MARKET_SCAN_LIST_FETCH_FAILED`：TWSE/TPEX API 不可達，確認網路後重試。
2. `MARKET_SCAN_STOCK_ERROR`：個別股票估值例外，見 `system_logs` 中 event 欄位，其餘股票仍正常輸出。
3. DB 不可寫：確認 `--db-path` 指定的路徑可讀寫。
