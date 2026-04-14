# GitHub Copilot Workspace Instructions — Stock Monitor

## 鐵律：禁止自作主張（Never Add Unrequested Content）

這是本專案最高優先的行為約束，任何實作、腳本、訊息輸出都必須遵守：

1. **使用者說改什麼，就改什麼。** 絕對不可在使用者未要求的地方加上任何額外內容、前綴、後綴、標題、標籤、說明文字。
2. **驗證腳本的輸出必須 100% 等於生產程式碼的輸出。** 不許加任何 wrapper、label、`===`、`【】`、額外的行。
3. **傳遞給生產函式的參數，必須走相同的生產路徑取得**，不許直接寫字串字面量繞過應有的 template / function 呼叫。
4. **改 template 就改 template，不許同時改業務邏輯或輸出內容。** 若使用者只說「改成用 Jinja2」，格式和文字必須與改之前完全一致。
5. **腳本、測試若需要 label 用於 terminal 區分，只能寫在 terminal stdout，絕不能出現在 LINE 訊息或任何對外輸出中。**

違反以上任何一條，視為嚴重錯誤，必須立即回滾並說明原因。
