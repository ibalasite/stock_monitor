# SECURITY_AND_SECRETS - Stock Monitoring System

版本：v0.2  
日期：2026-04-14  
來源基準：`PDD_Stock_Monitoring_System.md`、`EDD_Stock_Monitoring_System.md`（v0.8）

## 1. 文件目的
定義金鑰、權限、日誌與資料保護要求，降低通知系統的憑證與資料風險。

## 2. 資產分級
1. 高敏感：
   - `LINE_CHANNEL_ACCESS_TOKEN`（或 alias token）
2. 中敏感：
   - `LINE_TO_GROUP_ID`
   - `pending_delivery_ledger.payload_json`
3. 一般：
   - `watchlist`、`valuation_snapshots`（仍需避免外洩）

## 3. 秘密管理規範
1. token 僅可存於環境變數或受控 secret manager。
2. `.env` 不得提交到版本庫。
3. 錯誤日誌不得輸出完整 token：
   - 只允許 masked 顯示（如前 4 + 後 2）。
4. `LinePushClient` 持有物件不得透過 `repr()` 或任何 `__str__` 路徑輸出 token 明文；
   實作上必須設定 `field(repr=False)` 或等效保護（對應 EDD §13.1 CR-SEC-01）。
5. 系統時區名稱若設定無效，必須在啟動時即 `raise ValueError`；
   不得靜默 fallback 至 UTC，否則導致 +08:00 偏移錯誤（對應 EDD §13.1 CR-SEC-03）。
6. HTTP 回應讀取需設大小上限（預設 `MAX_RESPONSE_BYTES = 1_048_576`，即 1 MB）；
   防止超大回應耗盡記憶體（對應 EDD §13.1 CR-SEC-04）。
7. 發現 token 外洩時需立即輪替並重啟服務。

## 4. LINE 權限與配置
1. 使用 LINE Messaging API，不使用已終止的 LINE Notify。
2. Bot 僅加入指定群組，避免過度廣播。
3. 啟動前必做 token/groupId 驗證（fail-fast）。

## 5. SQLite 與檔案安全
1. DB 與 logs 目錄需限制最小權限（僅執行帳號可讀寫）。
2. `pending_delivery.jsonl` 可能含通知內容，需納入存取控制。
3. 備份資料需加密或存放在受控位置。

## 6. 日誌安全規範
1. `system_logs` 可記錄錯誤碼與事件，不記錄 secret 明文。
2. 建議結構化欄位：
   - `event`
   - `stock_no`（可選）
   - `minute_bucket`（可選）
   - `error_code`
3. 禁止寫入：
   - token 原文
   - 任何可直接重放 API 的憑證

## 7. 安全事件處置
1. Token 洩漏：
   - 立即撤銷舊 token
   - 產新 token 並更新環境
   - 重新啟動與驗證
2. 異常通知行為（大量發送）：
   - 檢查 cooldown 是否失效
   - 暫停發送功能
   - 回溯最近部署與設定變更

## 8. BDD/Spec 對齊安全驗收
1. 啟動缺參數必 fail-fast（`TP-ENV-003`）。
2. log 不可洩漏 token（`TP-ENV-003`）。
3. LINE 失敗不落 `message`（`TP-INT-004`）。
4. 補償流程不重複通知（`TP-INT-005`、`TP-INT-006`）。
5. `LinePushClient` repr/str 不洩漏 token 明文（`TP-SEC-001`）。
6. 無效時區名稱啟動時 fail-fast，不得靜默 fallback（`TP-SEC-002`）。
7. HTTP 回應有大小上限，防止 OOM（`TP-SEC-003`）。

## 9. 後續強化建議
1. token 週期性輪替（例如每 90 天）。
2. 增加 secret 掃描（pre-commit + CI）。
3. 為運維指令與資料匯出加上審計記錄。
