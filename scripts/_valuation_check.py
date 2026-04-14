"""Confirm 14:00 valuation readiness."""
import datetime
import sqlite3
import zoneinfo

tz = zoneinfo.ZoneInfo("Asia/Taipei")
now = datetime.datetime.now(tz=tz)
print("現在時間:", now.strftime("%Y-%m-%d %H:%M:%S %Z"))
print("weekday:", now.weekday(), "(0=Mon … 4=Fri, 5=Sat, 6=Sun)")
print("是否交易日 (weekday<5):", now.weekday() < 5)
print()

conn = sqlite3.connect("data/stock_monitor.db")
today = now.strftime("%Y-%m-%d")

print("=== 今日估值 logs ===")
rows = conn.execute(
    "SELECT event, detail, created_at FROM system_logs "
    "WHERE event LIKE 'VALUATION_%' ORDER BY created_at DESC LIMIT 5"
).fetchall()
for r in rows:
    ts = datetime.datetime.fromtimestamp(r[2], tz=tz).strftime("%H:%M:%S")
    print(f"  {r[0]}  {r[1]}  @{ts}")
if not rows:
    print("  (尚無估值 log — 尚未跑過)")

print()
print("=== 最近估值快照（valuation_snapshots）===")
snap_rows = conn.execute(
    "SELECT trade_date, method_name, stock_no, fair_price, cheap_price "
    "FROM valuation_snapshots ORDER BY rowid DESC LIMIT 9"
).fetchall()
for r in snap_rows:
    print(f"  {r[0]}  {r[1]:<35}  {r[2]}  fair={r[3]}  cheap={r[4]}")
if not snap_rows:
    print("  (尚無快照)")

conn.close()
