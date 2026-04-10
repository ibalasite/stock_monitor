# Defect Log

## 狀態總覽
- Blocker: 0
- Critical: 0
- Major: 0
- Minor: 0

## Defect Items

| ID | Severity | Status | 類型 | 問題描述 | 影響 | 建議處置 |
|---|---|---|---|---|---|---|
| 無 | - | - | - | 目前無開放中的缺陷項 | - | - |

## 已關閉問題

| ID | Severity | Status | 問題描述 | 解法 |
|---|---|---|---|---|
| D-000 | Major | Closed | Coverage 未達標（85%） | 補齊 `monitoring_workflow/trading_session/runtime/health/metrics/policies/time_bucket` 分支測試後已達 `100%` |
| D-001 | Minor | Closed | `pytest-bdd/gherkin` 產生 `DeprecationWarning` | 於 `pytest.ini` 增加定向 `filterwarnings`，消除第三方已知噪音警告 |
| D-002 | Minor | Closed | `sqlite3 ResourceWarning: unclosed database` | 補齊測試中的 SQLite 連線 `close()`（`tests/test_db_schema_requirements.py`、`tests/test_env_requirements.py`） |
