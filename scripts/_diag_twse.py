import sys, time
sys.stdout.reconfigure(line_buffering=True)
print("START", flush=True)
from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
print("imported", flush=True)
t = time.time()
p = TwseRealtimeMarketDataProvider(timeout_sec=10)
try:
    r = p.get_market_index()
    print(f"ok {time.time()-t:.1f}s {r}", flush=True)
except Exception as e:
    print(f"error {time.time()-t:.1f}s {e}", flush=True)
