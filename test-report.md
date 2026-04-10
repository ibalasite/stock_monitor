# Test Report

## 1. 測試摘要
- 執行時間: 2026-04-10 16:04:33 +08:00
- 測試命令: `python -m pytest -q tests`
- 測試結果: `113 passed`
- Coverage Gate: `100%` (statement + branch)
- Gate 結論: `PASS`

## 2. Coverage 結果
- 目標門檻: `--cov-fail-under=100`
- 實際結果: `TOTAL 228 statements, 56 branches, 100.00%`
- 各模組 coverage: 全部 `100%`

## 3. CI 設定
- 新增: `.github/workflows/ci.yml`
- 觸發條件:
  - `push` (all branches)
  - `pull_request`
- CI 內容:
  - 安裝 `pytest`, `pytest-bdd`, `pytest-cov`
  - 執行 `python -m pytest -q tests`
  - 由 `pytest.ini` 套用 coverage gate

## 4. 已完成調整
- `pytest.ini` 已加入:
  - `--cov=stock_monitor`
  - `--cov-branch`
  - `--cov-report=term-missing`
  - `--cov-fail-under=100`
- 新增分支覆蓋測試: `tests/test_coverage_gate_branches.py`

## 5. 風險與備註
- 目前仍有測試 warning（不影響 gate）:
  - `gherkin_line.py` 的 `DeprecationWarning`
  - `sqlite3` 的 `ResourceWarning`（部分測試未顯式關閉連線）
- 以上 warning 已記錄於 `defect-log.md`，建議後續清理。