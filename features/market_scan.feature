# language: en
@stock_monitor @bdd @pdd @edd @test_plan @FR-19
Feature: 全市場估值掃描（FR-19 market-wide valuation scan）
  As 個人投資者
  I want 執行 scan-market 指令掃描全體上市上櫃股票的估值
  So that 可自動找出低估股並加入監控清單，或輸出 CSV 供進一步分析

  Rule: Symbol 契約 — TwseAllListedStocksProvider 模組可 import（TP-SCAN-001）

    @TP-SCAN-001 @contract
    Scenario: [TP-SCAN-001] TwseAllListedStocksProvider symbol contract
      Given TwseAllListedStocksProvider is importable from all_listed_stocks_twse module
      When TwseAllListedStocksProvider is instantiated
      Then get_all_listed_stocks method should exist on the provider

  Rule: Symbol 契約 — run_market_scan_job 與 MarketScanResult 可 import（TP-SCAN-002）

    @TP-SCAN-002 @contract
    Scenario: [TP-SCAN-002] run_market_scan_job and MarketScanResult symbol contract
      Given run_market_scan_job is importable from market_scan module
      And MarketScanResult is importable from market_scan module
      Then MarketScanResult should have scan_date total_stocks watchlist_upserted near_fair_count uncalculable_count output_dir fields

  Rule: 全市場掃描端對端驗收（TP-UAT-016）

    @TP-UAT-016 @UAT-016
    Scenario: [TP-UAT-016/UAT-016] scan-market 完整掃描路徑
      Given a fresh database is initialized for market scan
      And stocks provider supplies below_cheap stock "8881" and near_fair stock "8882"
      When run_market_scan_job is executed with stub provider and fresh database
      Then watchlist should contain stock "8881"
      And scan_results_above_cheap csv should exist in the output directory
      And scan_results_above_cheap csv should contain a row for stock "8882"
      And MarketScanResult watchlist_upserted should equal 1
      And MarketScanResult near_fair_count should equal 1
      And system_logs in database should have no LINE_SEND event
