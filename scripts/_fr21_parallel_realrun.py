"""FR-21 Real Run — ParallelFinancialDataProvider 實際執行驗證

驗證項目：
  1. 能正確 import 並建立 ParallelFinancialDataProvider.default()
  2. 三源同時觸發（wall-time < 單源 × 3）
  3. 從 DB cache 取出 get_avg_dividend 結果（0050 已有 FinMind cache）
  4. 三源均 cache miss/unavailable → 正確 raise ProviderUnavailableError
  5. _call_parallel 內部 fetched_at 比較邏輯

執行方式（在專案根目錄）：
  python3 scripts/_fr21_parallel_realrun.py
"""
from __future__ import annotations

import sys
import time
import threading

# 加入 project root 到 path
sys.path.insert(0, ".")

from stock_monitor.adapters.financial_data_fallback import ParallelFinancialDataProvider
from stock_monitor.adapters.financial_data_port import ProviderUnavailableError

DB = "data/stock_monitor.db"
OK = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = OK if ok else FAIL
    print(f"  {mark}  {label}" + (f"  → {detail}" if detail else ""))
    if not ok:
        sys.exit(1)


print("\n=== FR-21 ParallelFinancialDataProvider Real Run ===\n")

# ── 1. import + 建立 ────────────────────────────────────────────────────────
print("[1] import & default() 工廠方法")
p = ParallelFinancialDataProvider.default(db_path=DB)
check("ParallelFinancialDataProvider.default() 建立成功", p is not None)
check("providers 數量 = 3", len(p._providers) == 3, str(len(p._providers)))
names = [getattr(pr, "provider_name", "?") for pr in p._providers]
check(
    "provider_name 各不相同 (finmind/mops/goodinfo)",
    sorted(names) == ["finmind", "goodinfo", "mops"],
    str(names),
)

# ── 2. 平行執行計時（0050 有 FinMind cache，其他 miss → raise immediately）──
print("\n[2] 平行執行：get_avg_dividend('0050')")
t0 = time.monotonic()
try:
    result = p.get_avg_dividend("0050")
    elapsed = time.monotonic() - t0
    check(
        f"回傳非 None（FinMind cache 命中）",
        result is not None,
        f"avg_dividend={result:.4f}" if result else "None",
    )
    check(
        f"elapsed < 5s（三源同時觸發、MOPS/Goodinfo 快速 raise）",
        elapsed < 5.0,
        f"{elapsed:.3f}s",
    )
    print(f"       wall-time: {elapsed:.3f}s")
except ProviderUnavailableError as e:
    elapsed = time.monotonic() - t0
    # 若三源全 miss（網路隔離/無資料）→ 正確 raise，也算通過
    print(f"  {WARN}  ProviderUnavailableError (三源全 miss，符合規格): {e}")
    check("elapsed < 10s（即使全 miss 也不應無限等待）", elapsed < 10.0, f"{elapsed:.3f}s")

# ── 3. 三源全部 raise → 應 raise ProviderUnavailableError ──────────────────
print("\n[3] 三源全部 ProviderUnavailableError → re-raise")


class _DownProvider:
    provider_name = "down"

    def get_avg_dividend(self, stock_no, years=5):
        raise ProviderUnavailableError("test down")

    def get_eps_data(self, *a, **kw): raise ProviderUnavailableError("down")
    def get_balance_sheet_data(self, *a, **kw): raise ProviderUnavailableError("down")
    def get_pe_pb_stats(self, *a, **kw): raise ProviderUnavailableError("down")
    def get_price_annual_stats(self, *a, **kw): raise ProviderUnavailableError("down")
    def get_shares_outstanding(self, *a, **kw): raise ProviderUnavailableError("down")


pdown = ParallelFinancialDataProvider(providers=[_DownProvider(), _DownProvider(), _DownProvider()])
raised = False
try:
    pdown.get_avg_dividend("2330")
except ProviderUnavailableError:
    raised = True
check("三源全部 down → raise ProviderUnavailableError", raised)

# ── 4. 真正平行計時（mock 各 sleep 0.2s）──────────────────────────────────
print("\n[4] 平行執行計時（mock sleep=0.2s × 3）")
call_starts: list[float] = []
lock = threading.Lock()


class _SlowProvider:
    provider_name = "slow"

    def get_avg_dividend(self, stock_no, years=5):
        with lock:
            call_starts.append(time.monotonic())
        time.sleep(0.2)
        return 3.0

    def get_eps_data(self, *a, **kw): return None
    def get_balance_sheet_data(self, *a, **kw): return None
    def get_pe_pb_stats(self, *a, **kw): return None
    def get_price_annual_stats(self, *a, **kw): return None
    def get_shares_outstanding(self, *a, **kw): return None


pslow = ParallelFinancialDataProvider(providers=[_SlowProvider(), _SlowProvider(), _SlowProvider()])
t0 = time.monotonic()
pslow.get_avg_dividend("2330")
elapsed = time.monotonic() - t0
span = max(call_starts) - min(call_starts) if len(call_starts) == 3 else 9
check("三個 provider 都被呼叫", len(call_starts) == 3, str(len(call_starts)))
check(
    f"啟動時差 < 50ms（同時觸發）",
    span < 0.05,
    f"{span*1000:.1f}ms",
)
check(
    f"wall-time < 0.4s（並行，非 0.6s）",
    elapsed < 0.40,
    f"{elapsed:.3f}s",
)
print(f"       wall-time: {elapsed:.3f}s  |  start span: {span*1000:.1f}ms")

# ── 5. _provider_fetched_at 讀 DB ────────────────────────────────────────
print("\n[5] _provider_fetched_at 讀取 DB")
p_check = ParallelFinancialDataProvider.default(db_path=DB)
finmind_p = p_check._providers[0]  # FinMindFinancialDataProvider
ts = p_check._provider_fetched_at(finmind_p, "0050")
check("finmind/0050 fetched_at > 0（DB 有 cache）", ts > 0, str(ts))

ts_none = p_check._provider_fetched_at(finmind_p, "XXXX_NO_SUCH")
check("無 cache 股票 fetched_at = 0", ts_none == 0, str(ts_none))

# ── 完成 ──────────────────────────────────────────────────────────────────
print("\n=== 全部通過 ===\n")
