"""One-shot live price check for all watchlist stocks via both adapters."""
import time
import datetime
import zoneinfo

from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
from stock_monitor.adapters.market_data_yahoo import YahooFinanceMarketDataProvider

STOCKS = ["2330", "2348", "3293"]
TZ = zoneinfo.ZoneInfo("Asia/Taipei")

print(f"查詢時間: {datetime.datetime.now(tz=TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
print()

# ---- TWSE ----
print("=== TWSE MIS (a 欄位 委賣一) ===")
twse = TwseRealtimeMarketDataProvider()
t1 = time.time()
tq = twse.get_realtime_quotes(STOCKS)
t2 = time.time()
for s in STOCKS:
    q = tq.get(s)
    if q:
        ts = datetime.datetime.fromtimestamp(q["tick_at"], tz=TZ).strftime("%H:%M:%S")
        print(f"  {s}  ask={q['price']:>8.2f}  tick={ts}  ex={q.get('exchange','?')}")
    else:
        print(f"  {s}  N/A (盤後/停牌/cache 冷)")
print(f"  elapsed {t2-t1:.2f}s")

print()

# ---- Yahoo ----
print("=== Yahoo TW HTML scraping (委賣一 fallback regularMarketPrice) ===")
yahoo = YahooFinanceMarketDataProvider()
t3 = time.time()
yq = yahoo.get_realtime_quotes(STOCKS, exchange_map={})
t4 = time.time()
for s in STOCKS:
    q = yq.get(s)
    if q:
        ts = datetime.datetime.fromtimestamp(q["tick_at"], tz=TZ).strftime("%H:%M:%S")
        print(f"  {s}  ask={q['price']:>8.2f}  tick={ts}  name={q.get('name','')}")
    else:
        print(f"  {s}  N/A")
print(f"  elapsed {t4-t3:.2f}s")
