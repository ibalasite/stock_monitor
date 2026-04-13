# ADR - Architecture Decision Records

版本：v0.2  
日期：2026-04-14  
來源基準：`PDD_Stock_Monitoring_System.md`、`EDD_Stock_Monitoring_System.md`（v0.8）

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
