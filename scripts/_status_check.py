"""Quick DB status snapshot."""
import sqlite3
import datetime
import zoneinfo

conn = sqlite3.connect("data/stock_monitor.db")
tz = zoneinfo.ZoneInfo("Asia/Taipei")

msg_cols = [c[1] for c in conn.execute("PRAGMA table_info(message)").fetchall()]
log_cols = [c[1] for c in conn.execute("PRAGMA table_info(system_logs)").fetchall()]
print("message cols:", msg_cols)
print("system_logs cols:", log_cols)

print()
print("=== 最後 5 筆 message ===")
rows = conn.execute("SELECT * FROM message ORDER BY rowid DESC LIMIT 5").fetchall()
for r in rows:
    print(" ", dict(zip(msg_cols, r)))
if not rows:
    print("  (無記錄)")

print()
print("=== 最後 5 筆 system_logs ===")
rows = conn.execute("SELECT * FROM system_logs ORDER BY rowid DESC LIMIT 5").fetchall()
for r in rows:
    d = dict(zip(log_cols, r))
    ts_val = d.get("created_at") or d.get("ts") or 0
    try:
        ts = datetime.datetime.fromtimestamp(int(ts_val), tz=tz).strftime("%m-%d %H:%M:%S")
    except Exception:
        ts = str(ts_val)
    level = d.get("level", "?")
    msg = str(d.get("message", d))[:100]
    print(f"  [{level}] {msg}  @{ts}")
if not rows:
    print("  (無記錄)")

conn.close()
