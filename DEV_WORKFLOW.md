# DEV_WORKFLOW.md — 專案開發流程手冊

最後更新：2026-04-17（Asia/Taipei）

本文件定義此專案的兩條標準開發流程：
- **流程 A**：需求驅動開發（Feature Development）
- **流程 B**：Review 驅動修正（Code / Security / Architecture Review）

文件上下游依賴關係請參照 `DOC_ROADMAP.md`。

---

## 核心約束（兩條流程共用）

1. 每個 Step 結束都要 **git commit（不 push）**
2. 中途任何步驟發現問題 → **rollback 到上一個 commit**，不往前走
3. **push 只有人類確認後才執行**
4. **不允許跳步**：沒有上一步確認，不能進下一步
5. **文件更新順序嚴格依 `DOC_ROADMAP.md` Tier 順序**
6. 修正程式碼時不得引入新的 CR Finding

---

## 流程 A｜需求驅動開發（Feature Development）

### Step 1　提出需求，更新 PDD + EDD

- 人類向 AI 提出新需求
- AI 修改 `PDD_Stock_Monitoring_System.md`
  - 新增或修改 FR-xx
  - 標明版本與日期
- AI 修改 `EDD_Stock_Monitoring_System.md`
  - 針對每條新 FR 提出對應解法說明
  - 每條 FR 在 EDD 中必須有對應章節，說明：
    - 設計選擇與理由
    - 影響的模組 / 層次
    - 若有 CR 禁止清單變更，一併寫入 EDD
- 人類 review PDD + EDD，確認無誤

```
git commit：docs(pdd+edd): FR-xx 需求與設計
```

---

### Step 2　PDD + EDD 確認後，觸發下游文件更新

- AI 依 `DOC_ROADMAP.md` 同步所有直接依賴的下游文件
- 更新範圍（依 Tier 順序執行）：
  - `ADR.md`（若有新架構決策）
  - `API_CONTRACT.md`（若有 Port/Adapter 介面異動）
  - `NFR_SLI_SLO.md`（若有非功能需求異動）
  - `SECURITY_AND_SECRETS.md`（若有安全規範異動）
  - `OPERATIONS_RUNBOOK.md`（若有維運流程異動）
  - `CLAUDE.md` + `CODEX.md`（symbol contract / 禁止清單同步）
  - `.github/copilot-instructions.md`（鐵律有變更時）
- **此時不寫任何程式碼，不動 tests**

```
git commit：docs(downstream): 對齊 FR-xx PDD/EDD 變更
```

---

### Step 3　更新 USER_STORY + TEST_PLAN，人類 review

- AI 更新 `USER_STORY_ACCEPTANCE_CRITERIA.md`：新增對應 User Story + 驗收條件
- AI 更新 `TEST_PLAN.md`：新增 TP-ID 條目，標明測試類型與對應 FR-xx
- 人類 review 新增項目，確認覆蓋是否完整，補充細節

```
git commit：docs(test-plan+user-story): 新增 FR-xx 對應條目
```

---

### Step 4　撰寫 BDD tests + Unit/Integration tests（先紅燈）

- AI 更新或新增 `features/*.feature`（依新 scenario）
- AI 撰寫 `tests/bdd/` glue steps
- AI 撰寫 `tests/test_*.py`（unit / integration）
- **所有新測試必須先「紅燈」**，確認測試確實在驗證新功能
- AI 回報完整紅燈清單（列出失敗的 test ID 與失敗原因）
- 人類確認測試設計正確，紅燈原因符合預期

```
git commit：test(red): FR-xx BDD + unit tests 紅燈基線
```

---

### Step 5　人類確認後，實作程式碼（紅燈轉綠燈）

- AI 依 TDD 小步前進：每次只讓最少量的紅燈變綠燈
- 每個小步驟完成後執行 pytest，確認沒有產生新的紅燈
- 全部 tests pass，coverage gate 維持 100%

```
git commit：feat(FR-xx): 實作通過所有測試
```

---

### Step 6　全部 pass 後，依 DOC_ROADMAP 更新下游文件

- 更新架構圖：`docs/architecture/diagrams/*.md` → 重新產生 `images/*.png`
- 更新 HTML 文件網站：`docs/*.html` 同步最新規格與架構圖
- 更新 `test-report.md`（最新 pass 數 / coverage）
- 更新 `uat-signoff.md`（UAT 條目對齊）
- 更新 `README.md`（§1 現況數字、§5 已完成項目）

```
git commit：docs(sync): FR-xx 完成後文件全同步
```

---

### Step 7　真實試跑驗證

- AI 使用真實環境（非 mock/stub）執行本次完成的功能，執行方式與終端使用者相同
- 試跑範圍：本次 FR 新增或修改的所有 CLI 指令、工作流程、關鍵路徑
- 試跑中若發現問題：
  - **可自行修正的 bug** → AI 直接修正並重新試跑，修正完畢後回報結果與修正說明
  - **需求問題**（功能本身定義不正確）→ 停止，從 Step 1 更新 PDD 並重走完整流程
  - **工程問題**（設計或實作方向有根本缺陷）→ 停止，從 Step 1 更新 EDD 並重走完整流程
- 試跑無問題後，AI 回報：
  - 執行的指令與參數
  - 實際輸出（stdout / log / 產生的檔案）
  - 結論：是否符合 PDD 驗收條件
- 人類確認試跑結果無誤後，進入下一步

```
（本 Step 不產生新 commit；若試跑中有 bug fix，另開 git commit：fix(FR-xx): 試跑修正）
```

---

### Step 8　請人類確認是否 push

- 人類確認 → `git push`
- 人類發現問題 → `git rollback` 到指定 commit，重新從該 Step 開始

---

## 流程 B｜Review 驅動修正（Code / Security / Architecture Review）

### Step 1　執行 Review

以下任一種情境觸發：

- **Code Review**：逐模組審查實作品質、可讀性、覆蓋率
- **Security Review**：依 OWASP Top 10 + `SECURITY_AND_SECRETS.md` 審查
- **Architecture Review**：依 `ADR.md` + EDD Clean Arch 原則審查

---

### Step 2　整理 Finding 清單

每條 Finding 標明：
- Finding ID（如 CR-SEC-01、CR-ARCH-01）
- 問題描述
- 影響範圍（模組 / 層次 / 文件）
- 嚴重等級（Blocker / Major / Minor）

AI 將所有 Finding 回報人類確認。

---

### Step 3　人類確認後，判斷性質分流

#### 分支 B1：工程 / 架構 / 安全相關 Finding

- AI 將 Finding 寫入 `EDD_Stock_Monitoring_System.md`
  - 新增或更新對應設計章節（說明正確設計方向）
  - 若屬禁止事項，寫入 EDD CR 禁止清單
  - 更新 `CLAUDE.md` + `CODEX.md` §12 禁止清單
- 人類 review EDD 更新內容，確認無誤

```
git commit：docs(edd): CR-xx Finding 寫入設計文件
```

→ 繼續往 **Step 4** 執行（路徑與流程 A Step 2 相同）

#### 分支 B2：純文件錯誤（筆誤 / 格式 / 描述不清）

- AI 直接修正對應文件
- 人類確認修正內容

```
git commit：docs(fix): 文件筆誤修正
```

→ 結束，不需走後續步驟

---

### Step 4　觸發下游文件更新

- AI 依 `DOC_ROADMAP.md` 同步所有直接依賴的下游文件
- 更新範圍：
  - `API_CONTRACT.md`（若有介面修正）
  - `NFR_SLI_SLO.md`（若有非功能需求調整）
  - `SECURITY_AND_SECRETS.md`（若有安全規範新增）
  - `OPERATIONS_RUNBOOK.md`（若有維運流程修正）
  - `CLAUDE.md` + `CODEX.md`（禁止清單 / symbol contract 同步）
- **此時不寫任何程式碼**

```
git commit：docs(downstream): 對齊 CR-xx EDD 變更
```

---

### Step 5　更新 TEST_PLAN，人類 review

- AI 更新 `TEST_PLAN.md`：新增 TP-ID 條目對應 CR Finding（補齊原本未覆蓋的場景）
- 人類 review 新增測試條目，確認覆蓋完整

```
git commit：docs(test-plan): 新增 CR-xx 對應 TP 條目
```

---

### Step 6　撰寫新 tests（先紅燈）

- AI 新增 `tests/test_cr_*.py` 或更新既有測試
- **所有新測試必須先「紅燈」**
- AI 回報完整紅燈清單（列出失敗的 test ID 與失敗原因）
- 人類確認紅燈原因符合 Finding 預期

```
git commit：test(red): CR-xx 紅燈基線
```

---

### Step 7　人類確認後，修正程式碼（紅燈轉綠燈）

- AI 依 TDD 小步修正：每次只讓最少量的紅燈變綠燈
- 修正過程中不得引入新的 CR Finding（不能以一個 CR 換另一個 CR）
- 全部 tests pass，coverage gate 維持 100%

```
git commit：fix(CR-xx): 修正通過所有測試
```

---

### Step 8　全部 pass 後，依 DOC_ROADMAP 更新下游文件

- 更新架構圖（若架構有異動）：`diagrams/*.md` → 重新產生 `images/*.png`
- 更新 HTML 文件網站：`docs/*.html`
- 更新 `test-report.md`
- 更新 `defect-log.md`（記錄 Finding 已解決）
- 更新 `README.md`（§1 現況數字）

```
git commit：docs(sync): CR-xx 完成後文件全同步
```

---

### Step 9　真實試跑驗證

- AI 使用真實環境（非 mock/stub）執行本次完成的功能，執行方式與終端使用者相同
- 試跑範圍：本次 FR / CR 新增或修改的所有 CLI 指令、工作流程、關鍵路徑
- 試跑中若發現問題：
  - **可自行修正的 bug** → AI 直接修正並重新試跑，修正完畢後回報結果與修正說明
  - **需求問題**（功能本身定義不正確）→ 停止，退出當前流程，以此問題重新開立 Flow A，從 Step 1 更新 PDD 開始
  - **工程問題**（設計或實作方向有根本缺陷）→ 停止，退出當前流程，以此問題重新開立 Flow A，從 Step 1 更新 EDD 開始
- 試跑無問題後，AI 回報：
  - 執行的指令與參數
  - 實際輸出（stdout / log / 產生的檔案）
  - 結論：是否符合 PDD 驗收條件
- 人類確認試跑結果無誤後，進入下一步

```
（本 Step 不產生新 commit；若試跑中有 bug fix，另開 git commit：fix(FR/CR-xx): 試跑修正）
```

---

### Step 10　請人類確認是否 push

- 人類確認 → `git push`
- 人類發現問題 → `git rollback` 到指定 commit，重新從該 Step 開始
