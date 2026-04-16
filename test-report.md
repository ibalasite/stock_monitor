# Test Report

## 1. 測試摘要
- 執行時間: 2026-04-17 +08:00
- 測試命令: `python -m pytest -q tests`
- 測試結果: `335 passed`
- Coverage Gate: `100%` (statement + branch)
- Gate 結論: `PASS`

## 2. Coverage 結果
- 目標門檻: `--cov-fail-under=100`
- 實際結果: `TOTAL 1436 statements, 364 branches, 100.00%`
- 各模組 coverage: 全部 `100%`

## 3. CI 設定
- 新增: `.github/workflows/ci.yml`
- 觸發條件:
  - `push` (all branches)
  - `pull_request`
- CI 內容:
  - pinned `actions/checkout` 與 `actions/setup-python` commit SHA
  - 安裝鎖版依賴 `requirements-dev.txt`（`--require-hashes`）
  - 執行 `pip-audit` 供應鏈掃描
  - 執行 `python -m pytest -q tests`
  - 由 `pytest.ini` 套用 coverage gate

## 4. 已完成調整
- `pytest.ini` 已加入:
  - `--cov=stock_monitor`
  - `--cov-branch`
  - `--cov-report=term-missing`
  - `--cov-fail-under=100`
- 新增分支覆蓋測試: `tests/test_coverage_gate_branches.py`
- 補充 timezone fallback 分支覆蓋: `tests/test_trading_bucket_kpi_rules.py`
- 新增 production adapters + runtime app + E2E smoke BDD 測試覆蓋
- BDD 與規格對齊至三方法基線：`emily_composite_v1` / `oldbull_dividend_yield_v1` / `raysky_blended_margin_v1`
- 新增估值案例：`TP-VAL-004`、`TP-VAL-005`、`TP-VAL-006`
- 新增 UAT 案例：`TP-UAT-012`
- 新增開盤摘要案例：`TP-INT-012`、`TP-UAT-013`
- 新增文件對齊案例：`TP-UAT-014`（所有 LINE 出站訊息模板化）
- 新增全市場掃描案例：`TP-SCAN-001~006`、`TP-UAT-016`（`scan-market` CLI、watchlist upsert、CSV 輸出、無 LINE 推播）
- 新增掃描注入案例：`TP-SCAN-007`（CLI 必須注入 DB 啟用方法；空方法 fail-fast）

## 5. 風險與備註
- 本次執行維持 `335 passed` 與 `100%` coverage gate，非阻斷 warning 不影響 gate 結果。
- 目前可直接作為 CI 與 UAT 簽核基準版本。
