"""Quick status check for the running daemon."""
import sqlite3
import datetime
import time

conn = sqlite3.connect("data/stock_monitor.db")
conn.row_factory = sqlite3.Row
tz8 = datetime.timezone(datetime.timedelta(hours=8))
now_ts = datetime.datetime.now(tz=tz8).strftime("%H:%M:%S")
print(f"=== status check @ {now_ts} ===\n")

print("--- 最近 15 筆 system_logs ---")
rows = conn.execute(
    "SELECT level, event, detail, created_at FROM system_logs ORDER BY id DESC LIMIT 15"
).fetchall()
for r in rows:
    ts = datetime.datetime.fromtimestamp(r["created_at"], tz=tz8).strftime("%H:%M:%S")
    detail = (r["detail"] or "")[:70]
    print(f"  {ts} [{r['level']}] {r['event']}  {detail}")

print()
print("--- 今日觸發通知 (2026-04-14) ---")
rows = conn.execute(
    "SELECT minute_bucket, stock_no, stock_status, message FROM message "
    "WHERE minute_bucket >= '2026-04-14' ORDER BY minute_bucket DESC LIMIT 20"
).fetchall()
if rows:
    for r in rows:
        print(f"  {r['minute_bucket']}  {r['stock_no']}  status={r['stock_status']}  {r['message'][:50]}")
else:
    print("  (無訊號 — 今日股價均高於門檻)")

print()
print("--- 即時報價探查 ---")
from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
provider = TwseRealtimeMarketDataProvider()
stock_nos = ["2330", "2348", "3293"]
try:
    quotes = provider.get_realtime_quotes(stock_nos)
    for sno in stock_nos:
        q = quotes.get(sno)
        if q:
            ts = datetime.datetime.fromtimestamp(q["tick_at"], tz=tz8).strftime("%H:%M:%S")
            print(f"  {sno}  price={q['price']}  tick={ts}")
        else:
            print(f"  {sno}  (無成交價 — 可能尚未成交或報價延遲)")
except Exception as exc:
    print(f"  TWSE query failed: {exc}")

conn.close()
