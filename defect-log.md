# Defect Log

## 狀態總覽
- Blocker: 0
- Critical: 0
- Major: 0
- Minor: 2

## Defect Items

| ID | Severity | Status | 類型 | 問題描述 | 影響 | 建議處置 |
|---|---|---|---|---|---|---|
| D-001 | Minor | Open | Test Tooling | `pytest-bdd/gherkin` 產生 `DeprecationWarning`（`maxsplit` positional argument） | 不影響測試結果，但輸出噪音高 | 升級/鎖版 `pytest-bdd` 與其相依套件，或在測試設定中過濾該 warning |
| D-002 | Minor | Open | Test Code Hygiene | 部分 schema 測試出現 `sqlite3 ResourceWarning: unclosed database` | 不影響功能驗證，但可能隱藏真實 warning | 在對應測試補上 `conn.close()` 或改用 fixture + teardown 管理連線 |

## 已關閉問題

| ID | Severity | Status | 問題描述 | 解法 |
|---|---|---|---|---|
| D-000 | Major | Closed | Coverage 未達標（85%） | 補齊 `monitoring_workflow/trading_session/runtime/health/metrics/policies/time_bucket` 分支測試後已達 `100%` |