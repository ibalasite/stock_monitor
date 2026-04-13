# language: en
@stock_monitor @bdd @TP-INT-012 @UAT-013
Feature: Opening Summary Runtime Behavior
  Scenario: Send opening summary at first trading minute even without threshold hits
    Given trading day opening minute at "2026-04-14 09:00"
    And watchlist has stocks "2330,2348,3293"
    And market quotes do not hit manual thresholds
    When execute one minute monitoring cycle with valuation snapshots enabled
    Then line client should receive one opening summary message
    And opening summary should include stock list and method list

