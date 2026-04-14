# language: en
@stock_monitor @bdd @pdd @edd @test_plan @market_data
Feature: 雙行情來源 Composite Adapter（TWSE 主 + Yahoo Finance 副）
  As 系統開發者與維運者
  I want 行情資料採 TWSE MIS 為主、Yahoo Finance 為副，以較新的 tick_at 為準
  So that 即使 TWSE 當下快照 z='-'（兩筆成交之間），系統仍能取得最接近現實的最後成交價，
          且 Yahoo API 失敗不中斷主流程

  Background:
    Given TWSE MIS endpoint 為 "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    And Yahoo Finance v8 chart endpoint 為 "https://query2.finance.yahoo.com/v8/finance/chart/"
    And TSE 上市股票 symbol 後綴為 ".TW"，OTC 上櫃後綴為 ".TWO"
    And 兩者 HTTP 回應讀取上限均為 1 MB（MAX_RESPONSE_BYTES）

  Rule: TWSE _price_cache 行為（TP-ADP-003）

    @TP-ADP-003a
    Scenario: [TP-ADP-003a] TWSE z 有成交值時更新 cache，回傳含 exchange 欄位
      Given TWSE API 對 2330 回傳 ex="tse", z="2045.0", tlong="1776100000000"
      When 呼叫 TwseRealtimeMarketDataProvider.get_realtime_quotes(["2330"])
      Then quotes["2330"]["price"] == 2045.0
      And quotes["2330"]["exchange"] == "tse"
      And quotes["2330"]["tick_at"] == 1776100000
      And _price_cache["2330"] == 2045.0

    @TP-ADP-003b
    Scenario: [TP-ADP-003b] TWSE z='-' 時使用 _price_cache 的最後已知成交價
      Given 前一輪 TWSE 回傳 z="2045.0" for 2330，_price_cache["2330"] = 2045.0
      And 本輪 TWSE API 對 2330 回傳 z="-", tlong="1776100060000"
      When 呼叫 TwseRealtimeMarketDataProvider.get_realtime_quotes(["2330"])
      Then quotes["2330"]["price"] == 2045.0
      And tick_at 為 cache 存入時的時間戳（不是本輪 tlong）

    @TP-ADP-003c
    Scenario: [TP-ADP-003c] TWSE z='-' 且 _price_cache 為空（冷啟動）時不加入 quotes
      Given _price_cache 為空
      And TWSE API 對 2330 回傳 z="-"
      When 呼叫 TwseRealtimeMarketDataProvider.get_realtime_quotes(["2330"])
      Then "2330" 不在 quotes dict 中

  Rule: Yahoo Finance Adapter 行為（TP-ADP-001）

    @TP-ADP-001a
    Scenario: [TP-ADP-001a] Yahoo API 正常回傳時取得 regularMarketPrice 與 regularMarketTime
      Given Yahoo Finance v8 chart API 對 2330.TW 回傳 regularMarketPrice=2035.0, regularMarketTime=1776100020
      And exchange_map = {"2330": "tse"}
      When 呼叫 YahooFinanceMarketDataProvider.get_realtime_quotes(["2330"])
      Then quotes["2330"]["price"] == 2035.0
      And quotes["2330"]["tick_at"] == 1776100020

    @TP-ADP-001b
    Scenario: [TP-ADP-001b] Yahoo API HTTP 4xx/5xx 失敗時寫 WARN log 且回傳空 dict
      Given Yahoo Finance API 對 2330.TW 回傳 HTTP 404
      When 呼叫 YahooFinanceMarketDataProvider.get_realtime_quotes(["2330"])
      Then 回傳空 dict {}
      And system_logs 應新增 level "WARN" 包含 "YAHOO_FETCH_WARN"
      And 主流程不中斷（無 exception 向上傳播）

    @TP-ADP-001c
    Scenario: [TP-ADP-001c] Yahoo API timeout 失敗時寫 WARN log 且回傳空 dict
      Given Yahoo Finance API 呼叫逾時（socket.timeout）
      When 呼叫 YahooFinanceMarketDataProvider.get_realtime_quotes(["2330"])
      Then 回傳空 dict {}
      And system_logs 應新增 level "WARN" 包含 "YAHOO_FETCH_WARN"

    @TP-ADP-001d
    Scenario: [TP-ADP-001d] OTC 上櫃股票使用 .TWO symbol 查詢
      Given exchange_map = {"3293": "otc"}
      And Yahoo Finance API 對 3293.TWO 回傳 regularMarketPrice=766.0, regularMarketTime=1776100020
      When 呼叫 YahooFinanceMarketDataProvider.get_realtime_quotes(["3293"])
      Then 請求 URL 包含 "3293.TWO"
      And quotes["3293"]["price"] == 766.0

    @TP-ADP-001e
    Scenario: [TP-ADP-001e] 無 exchange_map 時 fallback 使用 .TW symbol
      Given exchange_map = {}（無 3293 映射）
      And Yahoo Finance API 對 3293.TW 回傳任意值
      When 呼叫 YahooFinanceMarketDataProvider.get_realtime_quotes(["3293"])
      Then 請求 URL 包含 "3293.TW"（fallback）

  Rule: Composite Freshness-First 合併邏輯（TP-ADP-002）

    @TP-ADP-002a
    Scenario: [TP-ADP-002a] TWSE tick_at 較新時採用 TWSE 報價
      Given TWSE quotes["2330"]["price"] = 2045.0, tick_at = 1776100060
      And Yahoo quotes["2330"]["price"] = 2035.0, tick_at = 1776100020
      When 呼叫 CompositeMarketDataProvider.get_realtime_quotes(["2330"])
      Then result["2330"]["price"] == 2045.0
      And result["2330"]["tick_at"] == 1776100060
      And 來源標記為 "twse"（或等效）

    @TP-ADP-002b
    Scenario: [TP-ADP-002b] Yahoo tick_at 較新時採用 Yahoo 報價
      Given TWSE quotes["2330"]["price"] = 2000.0, tick_at = 1776099960（較舊）
      And Yahoo quotes["2330"]["price"] = 2045.0, tick_at = 1776100020（較新）
      When 呼叫 CompositeMarketDataProvider.get_realtime_quotes(["2330"])
      Then result["2330"]["price"] == 2045.0
      And result["2330"]["tick_at"] == 1776100020

    @TP-ADP-002c
    Scenario: [TP-ADP-002c] tick_at 相同時以 TWSE 為準
      Given TWSE quotes["2330"]["price"] = 2045.0, tick_at = 1776100020
      And Yahoo quotes["2330"]["price"] = 2044.0, tick_at = 1776100020
      When 呼叫 CompositeMarketDataProvider.get_realtime_quotes(["2330"])
      Then result["2330"]["price"] == 2045.0
      And 來源為 TWSE（相等時 TWSE 優先）

    @TP-ADP-002d
    Scenario: [TP-ADP-002d] TWSE cache 為空（冷啟動），Yahoo 有值時採用 Yahoo
      Given TWSE _price_cache 為空，get_realtime_quotes 回傳 {}
      And Yahoo quotes["2330"]["price"] = 2035.0, tick_at = 1776100020
      When 呼叫 CompositeMarketDataProvider.get_realtime_quotes(["2330"])
      Then result["2330"]["price"] == 2035.0
      And result["2330"]["tick_at"] == 1776100020

    @TP-ADP-002e
    Scenario: [TP-ADP-002e] TWSE 與 Yahoo 均無法取得報價時不加入結果 dict
      Given TWSE _price_cache 為空，回傳 {}
      And Yahoo API 失敗，回傳 {}
      When 呼叫 CompositeMarketDataProvider.get_realtime_quotes(["2330"])
      Then "2330" 不在 result dict 中
      And 呼叫端 runtime_service 應觸發 STALE_QUOTE:2330

    @TP-ADP-002f
    Scenario: [TP-ADP-002f] Yahoo 失敗時 Composite 仍能使用 TWSE cache 值繼續運作
      Given TWSE quotes["2330"]["price"] = 2045.0, tick_at = 1776100060（cache hit）
      And Yahoo API 失敗（timeout），回傳 {}
      When 呼叫 CompositeMarketDataProvider.get_realtime_quotes(["2330"])
      Then result["2330"]["price"] == 2045.0
      And 主流程正常繼續（不中斷）
      And system_logs 有 WARN 含 "YAHOO_FETCH_WARN"

    @TP-ADP-002g
    Scenario: [TP-ADP-002g] get_market_snapshot delegate 給 TWSE primary
      Given CompositeMarketDataProvider 注入 TWSE primary 與 Yahoo secondary
      When 呼叫 CompositeMarketDataProvider.get_market_snapshot(now_epoch=1776100000)
      Then 行為與直接呼叫 TwseRealtimeMarketDataProvider.get_market_snapshot 相同
      And snapshot 包含 "index_tick_at" 與 "index_price"

  Rule: HTTP 回應大小上限（TP-ADP-004）

    @TP-ADP-004
    Scenario: [TP-ADP-004] Yahoo adapter HTTP 回應受 MAX_RESPONSE_BYTES（1 MB）限制
      Given Yahoo Finance API 回應超過 1 MB
      When 呼叫 YahooFinanceMarketDataProvider._http_get_json
      Then 讀取以 MAX_RESPONSE_BYTES 截止（不發生無限記憶體占用）
      And 若截斷後 JSON 無效，寫 WARN 並回傳空 dict
