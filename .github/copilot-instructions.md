# GitHub Copilot Workspace Instructions — Stock Monitor

## 規格根源宣告

本專案以 `PDD_Stock_Monitoring_System.md` 為最高權威根源。所有工程設計、測試規格、BDD feature 都以 PDD 為上游，衝突時以 PDD 為準。

**正確優先順序：**
```
PDD > EDD > USER_STORY_ACCEPTANCE_CRITERIA > TEST_PLAN > features + tests
```

## 鐵律：禁止自作主張（Never Add Unrequested Content）

這是本專案最高優先的行為約束，任何實作、腳本、訊息輸出都必須遵守：

1. **使用者說改什麼，就改什麼。** 絕對不可在使用者未要求的地方加上任何額外內容、前綴、後綴、標題、標籤、說明文字。
2. **驗證腳本的輸出必須 100% 等於生產程式碼的輸出。** 不許加任何 wrapper、label、`===`、`【】`、額外的行。
3. **傳遞給生產函式的參數，必須走相同的生產路徑取得**，不許直接寫字串字面量繞過應有的 template / function 呼叫。
4. **改 template 就改 template，不許同時改業務邏輯或輸出內容。** 若使用者只說「改成用 Jinja2」，格式和文字必須與改之前完全一致。
5. **腳本、測試若需要 label 用於 terminal 區分，只能寫在 terminal stdout，絕不能出現在 LINE 訊息或任何對外輸出中。**

違反以上任何一條，視為嚴重錯誤，必須立即回滾並說明原因。

## Code Review 改善禁止清單（財務資料三源平行備援，FR-21）

以下規則同步自 CLAUDE.md §12，與其保持一致：

- **禁止** 在 `SWRCacheBase._fetch` 中將 API 失敗（`_fetch_raw` 回傳 `None`）寫入 DB 快取；`None` 只能觸發 `ProviderUnavailableError`（CR-FIN-01）
- **禁止** `ParallelFinancialDataProvider._call` 以 sequential fallback 模式（P1 失敗才試 P2）執行；必須三源全部同時執行後比較 `fetched_at`（CR-FIN-02）
- **禁止** 三個財務資料 Adapter 使用相同 `provider_name`；允許值為 `'finmind'`、`'mops'`、`'goodinfo'`（CR-FIN-03）
- **禁止** `GoodinfoAdapter` cache stale 時同步阻塞等待 scraping 完成；stale 必須背景執行，miss 必須同步，兩者不可對調（CR-FIN-04）
- **禁止** 估值方法（`EmilyCompositeV1` 等）直接 import `FinMindFinancialDataProvider`；一律使用 `ParallelFinancialDataProvider`（CR-FIN-05）
