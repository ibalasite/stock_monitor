Feature: FinMind Financial Data SWR Cache
  台股財務估值資料採 FinMind API + SQLite SWR Cache（Stale-While-Revalidate）三層策略：
  L1 記憶體 → L2 DB 新鮮（≤ 15 天）→ L3 DB 陳舊（立即回傳 + 背景刷新）→ L4 API 擷取存 DB。

  Background:
    Given 一個 FinMindFinancialDataProvider 使用臨時 SQLite db_path
    And _fetch_finmind 已被 mock 以追蹤 API 呼叫次數

  # TP-FIN-001
  Scenario: Cache miss 時呼叫 API 並存 DB
    Given financial_data_cache 表中無 stock_no="2330", dataset="TaiwanStockDividend"
    When 呼叫 provider.get_avg_dividend("2330")
    Then _fetch_finmind 被呼叫 1 次
    And 回傳值不為 None
    And financial_data_cache 新增 1 筆（stock_no="2330", dataset="TaiwanStockDividend"）
    And 再次呼叫 get_avg_dividend("2330") 時 _fetch_finmind 呼叫次數仍為 1（L1 mem hit）

  # TP-FIN-002
  Scenario: Cache 新鮮（≤ 15 天）不呼叫 API
    Given financial_data_cache 中已有 stock_no="2330", dataset="TaiwanStockDividend"，fetched_at 為 7 天前
    When 呼叫 provider.get_avg_dividend("2330")
    Then _fetch_finmind 呼叫次數 = 0
    And 回傳值與快取資料一致

  # TP-FIN-003
  Scenario: Cache 陳舊（> 15 天）立即回傳並背景刷新
    Given financial_data_cache 中已有 stock_no="2330", dataset="TaiwanStockDividend"，fetched_at 為 20 天前
    And _fetch_finmind mock 回傳新資料並同時記錄時間戳
    When 呼叫 provider.get_avg_dividend("2330")
    Then 立即回傳舊值（不阻塞）
    And 最終 financial_data_cache 中 stock_no="2330" 的 fetched_at 已更新（背景刷新完成）
    And 相同 dataset 最多只啟動 1 個背景刷新執行緒

  # TP-FIN-004
  Scenario: DB 不可用（db_path=None）時降級呼叫 API
    Given 建立 FinMindFinancialDataProvider(db_path=None)
    When 呼叫 provider.get_avg_dividend("2330")
    Then _fetch_finmind 被呼叫 1 次
    And 回傳值不為 None
    And 無任何 DB 讀寫動作（無例外）

Feature: Valuation Methods Real Implementation
  三個實際估值方法（OldbullDividendYieldV1、EmilyCompositeV1、RayskyBlendedMarginV1）
  公式正確性與缺資料降級行為。

  # TP-MVAL-001
  Scenario: OldbullDividendYieldV1 公式驗證
    Given avg_dividend = 38.2
    When 呼叫 OldbullDividendYieldV1.compute(stock_no, trade_date, stub_provider)
    Then fair ≈ 764.0（誤差 ≤ 0.1）
    And cheap ≈ 636.67（誤差 ≤ 0.1）

  Scenario: OldbullDividendYieldV1 缺股利資料
    Given avg_dividend = None
    When 呼叫 OldbullDividendYieldV1.compute(stock_no, trade_date, stub_provider)
    Then 回傳 (None, None)

  # TP-MVAL-002
  Scenario: EmilyCompositeV1 完整 4 子法
    Given stub provider 提供股利法、歷年股價法、PE 法、PB 法完整輸入
    When 呼叫 EmilyCompositeV1.compute(stock_no, trade_date, stub_provider)
    Then fair = mean(4 子法 fair) * 0.9
    And cheap = mean(4 子法 cheap) * 0.9

  Scenario: EmilyCompositeV1 部分子法缺資料（EPS=None）
    Given stub provider 提供股利法與歷年股價法，eps_data=None（PE/PB 子法跳過）
    When 呼叫 EmilyCompositeV1.compute(stock_no, trade_date, stub_provider)
    Then fair = mean(2 個可用子法 fair) * 0.9（PE、PB 子法不計入）
    And 回傳值不為 None

  Scenario: EmilyCompositeV1 全子法缺資料
    Given stub provider 所有輸入均為 None
    When 呼叫 EmilyCompositeV1.compute(stock_no, trade_date, stub_provider)
    Then 回傳 (None, None)

  # TP-MVAL-003
  Scenario: RayskyBlendedMarginV1 NCAV 跳過（current_assets ≤ total_liabilities）
    Given stub provider 提供 PE + 股利 + PB 輸入，current_assets ≤ total_liabilities
    When 呼叫 RayskyBlendedMarginV1.compute(stock_no, trade_date, stub_provider)
    Then fair = median(PE_fair, div_fair, PB_fair)
    And cheap = fair * 0.9
    And NCAV 子法不計入 median

  Scenario: RayskyBlendedMarginV1 全子法缺資料
    Given stub provider 所有輸入均為 None
    When 呼叫 RayskyBlendedMarginV1.compute(stock_no, trade_date, stub_provider)
    Then 回傳 (None, None)
