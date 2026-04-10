# Stock Monitoring System - README

更新日期：2026-04-10  
專案目標：台股價格監控 + LINE 群組通知 + 每日估值 + SQLite 落盤 + 補償機制

## 1. 專案現況摘要
1. 規格文件已完成並完成主要一致性對齊（PDD/EDD/User Story/Test Plan/Feature）。
2. BDD 規格檔已完成：`features/stock_monitoring_system.feature`。
3. 目前已接上 `pytest-bdd` 的 `.feature` 執行層（`tests/bdd/` 骨架）。
4. `pytest-bdd` 已安裝，step definitions 已可執行（BDD 測試可完整跑完）。
5. `stock_monitor` 主程式套件已建立並實作核心測試契約。
6. 最新狀態：
   - `pytest -q tests`：`107 passed`（含 BDD + unit/integration/UAT contract）
7. 注意：
   - 目前仍有 `PytestUnknownMarkWarning`（feature tags 未註冊），不影響測試正確性。

## 2. 文件地圖（全部文件與用途）
| 文件 | 用途 | 何時使用 |
|---|---|---|
| `PDD_Stock_Monitoring_System.md` | 產品需求（業務規則、範圍、UAT） | 討論需求、調整產品方向時 |
| `EDD_Stock_Monitoring_System.md` | 工程設計（架構、流程、DB schema、規則落地） | 進入實作前、review 設計時 |
| `USER_STORY_ACCEPTANCE_CRITERIA.md` | User Story 與驗收條件 | 排優先序、拆工作項時 |
| `features/stock_monitoring_system.feature` | BDD 可讀規格（業務語言） | 與 PM/QA 對齊行為、做 BDD 驗收時 |
| `TEST_PLAN.md` | 測試策略與 TP 對照矩陣 | 實作測試、追蹤 coverage 與驗收時 |
| `API_CONTRACT.md` | Port/Adapter 與錯誤語意契約 | 寫應用層與基礎設施介面前 |
| `ADR.md` | 架構決策記錄（為什麼這樣設計） | 有架構爭議或變更時 |
| `NFR_SLI_SLO.md` | 非功能需求與量測指標 | 定義 KPI、監控與告警時 |
| `SECURITY_AND_SECRETS.md` | 金鑰與安全規範 | 上線前安全檢查與稽核 |
| `OPERATIONS_RUNBOOK.md` | 維運與故障排除流程 | 日常操作、事故處理時 |
| `CODEX.md` | Codex 執行手冊與 symbol contract | 使用 Codex 開發時 |
| `CLAUDE.md` | Claude 執行手冊（與 CODEX 同語意） | 使用 Claude 開發時 |
| `README.md` | 專案入口與進度看板 | 每次進入專案第一個讀 |

## 3. 開發方法（BDD + Spec/Spac-Driven + TDD）
1. Spec/Spac-Driven：先規格後程式，所有實作以 `PDD + EDD` 為母體。
2. BDD（外層）：先寫/修 `.feature`，再用 `pytest-bdd` 將 scenario 轉成可執行測試，先跑出 Red。
3. TDD（內層）：針對 domain/application 細節寫 pytest 測試，先 Red，再最小實作到 Green，最後 Refactor。
4. 驗收順序採 outside-in：`feature scenario` 綠燈 + `unit/integration` 綠燈，才算完成。

## 4. 標準開發流程（固定順序）
1. 更新規格：`PDD -> EDD -> ADR/API_CONTRACT`。
2. 更新行為：`.feature`。
3. 建立/更新 BDD glue（`pytest-bdd` scenario + steps），先跑 BDD Red。
4. 更新 `TEST_PLAN` 與內層 pytest 測試（unit/integration），先跑 TDD Red。
5. 實作主程式讓測試逐批轉綠（先 unit/integration，再回歸 BDD scenario）。
6. 重構與補文件。
7. 最終驗收：BDD scenario 全綠 + TP/UAT 全綠 + coverage gate。

## 5. 目前做到哪一步
### 已完成
1. 需求與設計文件齊備。
2. PDD/EDD 已對齊以下關鍵規則：
   - `idempotency_key = stock_no + minute_bucket`（不含 `stock_status`）
   - 冷卻鍵維持 `stock_no + stock_status`
   - LINE 參數 canonical + alias 相容規則
   - `MAX_RETRY_COUNT=3`、`STALE_THRESHOLD_SEC=90`
   - `methods_hit` 必須為 JSON array
3. BDD `.feature` 與 `TEST_PLAN` TP-ID 已對齊。
4. 內層 pytest 契約測試檔已建立（可作為 TDD 基線）。
5. `tests/bdd/` 骨架已建立，scenario glue 已可載入整份 `.feature`。
6. `tests/bdd` step definitions 已可執行，BDD 測試已全綠。
7. `stock_monitor` 套件已建立，必要 symbol 與核心流程已落地。

### 未完成
1. CI/coverage gate 尚未配置。
2. `test-report.md`、`defect-log.md`、`uat-signoff.md` 尚未產出。
3. pytest marks 註冊（消除 `PytestUnknownMarkWarning`）尚未配置。

## 6. 下一步要做什麼（建議執行順序）
1. 加上 `pytest.ini` 註冊 feature tags（消除 unknown mark warning）。
2. 設定 coverage gate（`lines/branches/functions/statements = 100%`）並納入 CI。
3. 產出交付文件：`test-report.md`、`defect-log.md`、`uat-signoff.md`。
4. 開始下一個功能增量時，維持流程：`PDD/EDD -> feature -> tests -> code`。

## 7. 常用命令
```powershell
# 跑全部測試
& 'C:\Users\ibala\AppData\Local\Programs\Python\Python313\python.exe' -m pytest -q tests

# 安裝 pytest-bdd（若尚未安裝）
& 'C:\Users\ibala\AppData\Local\Programs\Python\Python313\python.exe' -m pip install pytest-bdd

# 只跑 BDD 測試（建立後）
& 'C:\Users\ibala\AppData\Local\Programs\Python\Python313\python.exe' -m pytest -q tests/bdd

# 跑單一測試模組
& 'C:\Users\ibala\AppData\Local\Programs\Python\Python313\python.exe' -m pytest -q tests/test_policy_rules.py
```

## 8. 文件維護規則
1. 規格有變更時，必須同步更新：`PDD/EDD/feature/TEST_PLAN/CODEX/CLAUDE/README`。
2. 任何新功能都要有對應：
   - 至少一個 User Story
   - 至少一個 `.feature` Scenario
   - 至少一個 TP 測試案例
3. 未更新文件不得視為完成。
