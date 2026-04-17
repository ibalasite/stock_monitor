# DOC_ROADMAP.md — 文件上下游對應全覽

最後更新：2026-04-17（Asia/Taipei）

本文件定義專案所有文件的上下游依賴關係與責任歸屬。
開發原則：Spec-Driven Development + BDD（外層）+ TDD（內層）。

**衝突解法原則：下游要對齊上游，不是上游屈從下游。**
feature 若與 PDD 衝突 → 修 feature。tests 若與 EDD 衝突 → 修 tests。

---

## 凡例

| 符號 | 意義 |
|------|------|
| 人類主筆，AI 優化 | 人類撰寫需求/補充，AI 優化結構與格式 |
| AI 草稿，人類補充 | AI 依上游生成草稿，人類補充細節後確認 |
| AI 全自動 | AI 依上游自動生成，人類 review 即可 |
| `→` | 直接上游依賴 |

---

## Tier 0｜知識背景輸入

> 無上游。人類提供，作為 PDD 的輸入背景，不是規格本身。

| 文件 | 責任 | 流向 | 說明 |
|------|------|------|------|
| `VALUATION_PERSONAS_DISTILLATION.md` | 人類主筆，AI 優化 | → PDD FR-14~FR-16 | 艾蜜莉／股海老牛／雷司紀估值方法蒸餾，是 PDD 估值需求的輸入背景 |

---

## Tier 1｜產品需求（最高權威）

> 全專案所有文件的根源。衝突時，此層永遠優先。

| 文件 | 責任 | 上游 | 說明 |
|------|------|------|------|
| `PDD_Stock_Monitoring_System.md` | **人類主筆，AI 優化** | Tier 0 知識輸入 | FR-01~FR-18、UAT 範圍定義、業務規則、Out of Scope |

---

## Tier 2｜工程設計與驗收角度

> 直接依賴 PDD。PDD 確認後才能開始此層。

| 文件 | 責任 | 上游 | 說明 |
|------|------|------|------|
| `EDD_Stock_Monitoring_System.md` | AI 草稿，人類補充 | → PDD | Clean Arch 分層、流程設計、DB schema（7 表）、adapter 設計、CR 禁止清單 |
| `USER_STORY_ACCEPTANCE_CRITERIA.md` | AI 草稿，人類補充 | → PDD | PDD 每條 FR 的驗收視角拆解；與 PDD 是同一件事的不同角度，衝突時以 PDD 為準 |

---

## Tier 3｜測試策略

> 依賴 PDD + EDD。每個 TP-ID 必須可追溯回 FR-xx。

| 文件 | 責任 | 上游 | 說明 |
|------|------|------|------|
| `TEST_PLAN.md` | AI 草稿，人類補充 | → PDD + EDD | TP-ID 矩陣、測試類型分類（BDD/unit/integration/UAT）、coverage gate 定義 |

---

## Tier 4｜補充設計文件

> 依賴 PDD + EDD。各文件細化特定設計面向。

| 文件 | 責任 | 上游 | 說明 |
|------|------|------|------|
| `ADR.md` | AI 草稿，人類確認 | → PDD + EDD | 架構決策記錄；每條 ADR 對應 EDD 的設計選擇與理由 |
| `API_CONTRACT.md` | AI 草稿，人類確認 | → EDD adapter 設計 | Port/Adapter 介面與錯誤語意契約；是 EDD adapter 設計的可執行化宣告 |
| `NFR_SLI_SLO.md` | AI 草稿，人類確認 | → PDD NFR 章節 + EDD | 非功能需求量測指標（延遲 / 可用性 / SLO） |
| `SECURITY_AND_SECRETS.md` | AI 草稿，人類確認 | → PDD 安全需求 + OWASP Top 10 | 金鑰管理規範、CR-SEC-* 安全禁止清單 |
| `OPERATIONS_RUNBOOK.md` | AI 草稿，人類確認 | → EDD deployment 設計 | 維運操作手冊、啟動指令、故障排除 SOP |

---

## Tier 5｜BDD 可執行規格

> 依賴 PDD + TEST_PLAN。AI 生成草稿，人類補充 scenario 細節與驗收語言後確認。

| 文件 | 責任 | 上游 | 說明 |
|------|------|------|------|
| `features/stock_monitoring_system.feature` | AI 草稿，人類補充 | → PDD FR-* + TEST_PLAN TP-* | 主 BDD 規格，涵蓋所有核心 scenario |
| `features/stock_monitoring_smoke.feature` | AI 草稿，人類補充 | → EDD 關鍵路徑 | 關鍵流程 smoke 驗證 |
| `features/line_template_rendering.feature` | AI 草稿，人類補充 | → PDD FR-17 | LINE 訊息 template 渲染行為 |
| `features/line_template_fr17.feature` | AI 草稿，人類補充 | → PDD FR-17 | FR-17 細化場景 |
| `features/market_data_composite.feature` | AI 草稿，人類補充 | → EDD adapter 設計 + CR-ADP-02 | Composite 行情 Freshness-First 行為 |
| `features/opening_summary_runtime.feature` | AI 草稿，人類補充 | → PDD + EDD 開盤摘要流程 | 開盤摘要 runtime 行為 |
| `features/market_scan.feature` | AI 草稿，人類補充 | → PDD FR-19 + EDD §14 + TEST_PLAN TP-SCAN-* | 全市場估值掃描三分類行為 |
| `features/financial_data_finmind_swr.feature` | AI 草稿，人類補充 | → EDD §9.3/§16 + TEST_PLAN TP-FIN-*/TP-MVAL-* | FinMind SWR cache 三層策略 + 三方法公式 |

---

## Tier 6｜AI 執行手冊

> 依賴全部上游。供 AI agent 進入專案時使用的執行基線。

| 文件 | 責任 | 上游 | 說明 |
|------|------|------|------|
| `CLAUDE.md` | AI 維護 | → PDD > EDD > TEST_PLAN > features + tests | Claude agent 執行基線；含 symbol contract、禁止清單、優先順序宣告 |
| `CODEX.md` | AI 維護 | → PDD > EDD > TEST_PLAN > features + tests | 與 CLAUDE.md 同語意，對應 Codex agent |
| `.github/copilot-instructions.md` | AI 維護 | → 鐵律（禁止清單） | Copilot 鐵律核心；禁止清單優先於所有 AI 行為 |

**優先順序宣告（正確版）：**
```
PDD > EDD > USER_STORY_ACCEPTANCE_CRITERIA > TEST_PLAN > features + tests
```
衝突時下游對齊上游，不得反向。

---

## Tier 7｜TDD 測試

> AI 依 features + EDD 推導生成。每個測試必須可追溯回上游文件的具體章節。

| 文件 | 責任 | 上游 | 說明 |
|------|------|------|------|
| `tests/test_policy_rules.py` | AI 全自動 | → PDD §5 業務規則 + EDD | 訊號 / 冷卻 / 聚合政策 |
| `tests/test_integration_workflow.py` | AI 全自動 | → EDD 監控流程設計 | 完整分鐘週期整合 |
| `tests/test_db_schema_requirements.py` | AI 全自動 | → EDD schema（7 表） | DB schema 符合性驗證 |
| `tests/test_env_requirements.py` | AI 全自動 | → CLAUDE/CODEX §4 LINE config | 環境變數 fail-fast 規則 |
| `tests/test_uat_contract.py` | AI 全自動 | → PDD UAT 條目 + TEST_PLAN TP-UAT-* | UAT-001~014 逐條可追溯驗證 |
| `tests/test_valuation_job.py` | AI 全自動 | → PDD FR-14~FR-18 + EDD 估值設計 | 估值日結流程 |
| `tests/test_adapters_market_line.py` | AI 全自動 | → EDD adapter 設計 + API_CONTRACT | TWSE / LINE adapter |
| `tests/test_adapters_sqlite_repo.py` | AI 全自動 | → EDD schema + API_CONTRACT | SQLite repository |
| `tests/test_adapters_yahoo_composite_red.py` | AI 全自動 | → EDD CR-ADP-01/02 + API_CONTRACT | Yahoo / Composite adapter |
| `tests/test_trading_bucket_kpi_rules.py` | AI 全自動 | → PDD 交易時段常數 + CODEX §3 | 時段 / KPI 規則 |
| `tests/test_runtime_service_app.py` | AI 全自動 | → EDD runtime 設計 + CODEX §3 常數 | daemon 週期常數 |
| `tests/test_coverage_gate_branches.py` | AI 全自動 | → DoD 100% coverage | coverage gate 執行驗證 |
| `tests/test_line_template_red.py` | AI 全自動 | → features line_template_rendering + PDD FR-17 | TDD red phase — LINE template |
| `tests/test_line_template_fr17_red.py` | AI 全自動 | → features line_template_fr17 + PDD FR-17 | TDD red phase — FR-17 細化 |
| `tests/test_opening_summary_red.py` | AI 全自動 | → features opening_summary_runtime + EDD | TDD red phase — 開盤摘要 |
| `tests/test_valuation_phase2_red.py` | AI 全自動 | → PDD FR-14~16 + EDD 估值設計 | TDD red phase — 估值 Phase 2 |
| `tests/test_cr_actions_red.py` | AI 全自動 | → EDD CR 禁止清單 | CR 改善項目驗證 |
| `tests/test_cr_phase2_red.py` | AI 全自動 | → EDD CR 禁止清單 Phase 2 | CR Phase 2 改善項目驗證 |
| `tests/test_market_scan.py` | AI 全自動 | → PDD FR-19 + EDD §14 + TEST_PLAN TP-SCAN-* | 全市場估值掃描 unit / integration |
| `tests/test_market_scan_methods.py` | AI 全自動 | → EDD §9.1/§9.3 + TEST_PLAN TP-MVAL-* | 三方法公式 + load_enabled_scan_methods |
| `tests/test_finmind_swr_cache.py` | AI 全自動 | → EDD §9.3/§16 + TEST_PLAN TP-FIN-* | FinMind SWR cache 三層策略 |
| `tests/test_platform_fr20.py` | AI 全自動 | → EDD §15 + TEST_PLAN TP-PLAT-* | pathlib + SIGTERM 跨平台驗證 |
| `tests/bdd/` | AI 全自動 | → features/*.feature（全 8 個） | pytest-bdd glue steps |
| `tests/_contract.py` | AI 全自動 | → CLAUDE/CODEX §7 symbol contract | symbol 存在性契約基線 |

---

## Tier 8｜實作程式碼

> AI 撰寫，依 tests/ 驅動（TDD）。

| 目錄 / 檔案 | 責任 | 上游 |
|-------------|------|------|
| `stock_monitor/domain/` | AI 全自動 | → EDD 業務規則 + tests/domain |
| `stock_monitor/application/` | AI 全自動 | → EDD 應用層設計 + tests/application |
| `stock_monitor/adapters/` | AI 全自動 | → EDD adapter 設計 + API_CONTRACT |
| `stock_monitor/bootstrap/` | AI 全自動 | → EDD + SECURITY_AND_SECRETS |
| `stock_monitor/db/schema.py` | AI 全自動 | → EDD schema（7 表） |
| `stock_monitor/uat/scenarios.py` | AI 全自動 | → PDD UAT 條目 |
| `stock_monitor/app.py` | AI 全自動 | → EDD CLI 設計（Interface Layer 只做路由，禁止含業務邏輯） |

---

## Tier 9｜架構圖

> AI 依 EDD + 實作程式碼產生。Mermaid 原始檔 → PNG。

| 文件 | 責任 | 上游 |
|------|------|------|
| `docs/architecture/README.md` | AI 全自動 | → EDD + diagrams 目錄 |
| `docs/architecture/diagrams/01-system-context.md` → `images/01-system-context.png` | AI 全自動 | → EDD C4 L1 系統邊界（TWSE / Yahoo / LINE / SQLite） |
| `docs/architecture/diagrams/02-clean-architecture.md` → `images/02-clean-architecture.png` | AI 全自動 | → EDD Clean Arch 四層分層設計 |
| `docs/architecture/diagrams/03-intraday-flow.md` → `images/03-intraday-flow.png` | AI 全自動 | → EDD 盤中每分鐘監控流程（冷卻 / 冪等 / 補償） |
| `docs/architecture/diagrams/04-valuation-flow.md` → `images/04-valuation-flow.png` | AI 全自動 | → EDD 估值日結流程（三方法 + 狀態管理） |
| `docs/architecture/diagrams/05-market-data-adapter.md` → `images/05-market-data-adapter.png` | AI 全自動 | → EDD Freshness-First 聚合策略 + CR-ADP-02 |
| `docs/architecture/diagrams/06-data-model.md` → `images/06-data-model.png`、`images/06-data-model-2.png` | AI 全自動 | → EDD schema（8 表，含 financial_data_cache）；含 §6.3 補償流程 sequenceDiagram（→ `-2.png`） |
| `docs/architecture/diagrams/07-deployment.md` → `images/07-deployment.png`、`images/07-deployment-2.png` | AI 全自動 | → EDD deployment 設計 + OPERATIONS_RUNBOOK；含 §7.3 Process 生命週期 stateDiagram（→ `-2.png`） |
| `docs/architecture/diagrams/08-market-scan.md` → `images/08-market-scan.png`、`images/08-market-scan-2.png`、`images/08-market-scan-3.png` | AI 全自動 | → EDD §14.9/§14.10/§14.11（FR-19 架構圖、流程圖、循序圖） |

---

## Tier 10｜HTML 文件網站

> AI 依對應 .md 規格渲染為靜態 HTML。

| 文件 | 責任 | 上游 |
|------|------|------|
| `docs/pdd.html` | AI 全自動 | → `PDD_Stock_Monitoring_System.md` |
| `docs/edd.html` | AI 全自動 | → `EDD_Stock_Monitoring_System.md` + `docs/architecture/images/*.png` |
| `docs/testplan.html` | AI 全自動 | → `TEST_PLAN.md` |
| `docs/bdd.html` | AI 全自動 | → `features/*.feature`（全 8 個） |
| `docs/index.html` | AI 全自動 | → 彙整所有 Tier 的入口導覽，需與 README §2 保持同步 |
| `docs/site.css` | AI 全自動 | → 共用樣式，與文件內容無關 |

---

## Tier 11｜交付記錄

> AI 依執行結果產生。每次發版或週期結束後更新。

| 文件 | 責任 | 上游 |
|------|------|------|
| `test-report.md` | AI 全自動 | → `pytest` 執行結果快照 |
| `uat-signoff.md` | AI 全自動 | → TEST_PLAN TP-UAT-001~014 條目 |
| `defect-log.md` | AI 全自動 | → tests/ 失敗記錄 + 手動 QA 發現 |

---

## Tier 12｜專案入口

> AI 維護，反映全部層的摘要快照。

| 文件 | 責任 | 上游 | 說明 |
|------|------|------|------|
| `README.md` | AI 維護 | → 所有 Tier 的摘要 | §1 測試數 / coverage 需隨 test-report 同步；§4 開發流程順序必須與本文件一致 |

---

## 附錄：待決策項目

| # | 問題 | 說明 |
|---|------|------|
| 1 | `CLAUDE.md` / `CODEX.md` §1 優先順序 | ✅ 已修正：`PDD(1) > EDD(2) > USER_STORY(3) > TEST_PLAN(4) > features+tests(5)` |
| 2 | `docs/pdd.html` banner | ✅ 已修正：PDD 為根源（Tier 1），衝突以 PDD 為準。subtitle 更新為 FR-01～FR-18，補入 FR-18。 |
| 3 | `docs/edd.html` banner | ✅ 已修正：`PDD → EDD → USER_STORY → TEST_PLAN → features + tests`，下游對齊上游。 |
| 4 | `images/06-data-model-2.png`、`07-deployment-2.png` | ✅ 已確認：非孤立。由 `06-data-model.md` §6.3、`07-deployment.md` §7.3 第二個 Mermaid 區塊自動產生，`generate_arch_diagrams.mjs` 依序輸出 `-2.png`。不需另建獨立原始檔。 |
| 5 | `docs/bdd.html` | ✅ 已修正：補入 `line_template_fr17.feature` card，現共 8 個 feature（另補入 `market_scan.feature`、`financial_data_finmind_swr.feature`）。 |
