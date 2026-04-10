# language: en
@bdd @e2e_smoke @stock_monitor
Feature: Stock Monitoring Runtime Smoke
  As an operator
  I want end-to-end smoke checks on the runtime flow
  So that critical production paths are validated continuously

  Scenario: Skip polling outside trading session
    Given runtime time is "2026-04-10T13:31:00+08:00"
    And market snapshot is available
    And watchlist has stock "2330" fair 1500 cheap 1000
    And realtime quote for "2330" is 999
    When I execute one monitor cycle
    Then cycle result status should be "skipped"
    And cycle reason should be "non_trading_session"
    And line push count should be 0

  Scenario: Cooldown suppresses duplicate status notification
    Given runtime time is "2026-04-10T10:00:00+08:00"
    And market snapshot is available
    And watchlist has stock "2330" fair 1500 cheap 1000
    And realtime quote for "2330" is 1490
    And previous message for "2330" status 1 was sent 60 seconds ago
    When I execute one monitor cycle
    Then cycle result status should be "no_signal"
    And line push count should be 0

  Scenario: Reconcile pending item once without duplicate resend
    Given runtime time is "2026-04-10T10:00:00+08:00"
    And a pending compensation item exists
    When I execute reconcile cycle
    Then reconcile count should be 1
    When I execute reconcile cycle
    Then reconcile count should be 0
    And line push count should be 0
