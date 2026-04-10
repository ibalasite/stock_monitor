from __future__ import annotations

from ._contract import require_symbol


def test_tp_pol_001_status_priority_2_over_1():
    PriorityPolicy = require_symbol(
        "stock_monitor.domain.policies",
        "PriorityPolicy",
        "TP-POL-001",
    )
    policy = PriorityPolicy()
    result = policy.resolve_status([1, 2])
    assert result == 2, "[TP-POL-001] When status 1/2 coexist, effective status must be 2."


def test_tp_pol_002_cooldown_299s_block_301s_allow():
    CooldownPolicy = require_symbol(
        "stock_monitor.domain.policies",
        "CooldownPolicy",
        "TP-POL-002",
    )
    policy = CooldownPolicy(cooldown_seconds=300)
    now_ts = 1_712_710_600

    blocked = policy.can_send(last_sent_at=now_ts - 299, now_ts=now_ts)
    allowed = policy.can_send(last_sent_at=now_ts - 301, now_ts=now_ts)

    assert blocked is False, "[TP-POL-002] 299 seconds must still be in cooldown window."
    assert allowed is True, "[TP-POL-002] 301 seconds must be outside cooldown window."


def test_tp_pol_003_null_last_sent_is_sendable():
    CooldownPolicy = require_symbol(
        "stock_monitor.domain.policies",
        "CooldownPolicy",
        "TP-POL-003",
    )
    policy = CooldownPolicy(cooldown_seconds=300)
    assert policy.can_send(last_sent_at=None, now_ts=1_712_710_600) is True, (
        "[TP-POL-003] last_sent_at=None must be treated as sendable."
    )


def test_tp_pol_004_minute_idempotency_key_must_not_include_status():
    build_minute_idempotency_key = require_symbol(
        "stock_monitor.domain.idempotency",
        "build_minute_idempotency_key",
        "TP-POL-004",
    )
    key_status_1 = build_minute_idempotency_key(
        stock_no="2330",
        minute_bucket="2026-04-10 10:21",
        stock_status=1,
    )
    key_status_2 = build_minute_idempotency_key(
        stock_no="2330",
        minute_bucket="2026-04-10 10:21",
        stock_status=2,
    )
    assert key_status_1 == key_status_2, (
        "[TP-POL-004] Same stock_no+minute_bucket should generate same idempotency key, "
        "independent from stock_status."
    )
    assert key_status_1 == "2330|2026-04-10 10:21", (
        "[TP-POL-004] Expected normalized minute idempotency key format."
    )


def test_tp_pol_005_multi_method_all_status1_produces_single_stock_event():
    aggregate_stock_signals = require_symbol(
        "stock_monitor.domain.policies",
        "aggregate_stock_signals",
        "TP-POL-005",
    )
    hits = [
        {"stock_no": "2330", "stock_status": 1, "method": "manual_rule"},
        {"stock_no": "2330", "stock_status": 1, "method": "pe_band_v1"},
        {"stock_no": "2330", "stock_status": 1, "method": "pb_band_v2"},
    ]
    events = aggregate_stock_signals("2330", hits)
    assert len(events) == 1, (
        "[TP-POL-005] Multiple method hits for same stock should produce exactly one stock event."
    )
    event = events[0]
    assert event["stock_status"] == 1, (
        "[TP-POL-005] Aggregated event status must be 1 when all methods hit status 1."
    )
    methods = event["methods_hit"]
    assert "manual_rule" in methods and "pe_band_v1" in methods and "pb_band_v2" in methods, (
        "[TP-POL-005] methods_hit must list all matched methods."
    )


def test_tp_pol_006a_status2_sendable_after_status1_sent_60s_ago():
    CooldownPolicy = require_symbol(
        "stock_monitor.domain.policies",
        "CooldownPolicy",
        "TP-POL-006a",
    )
    policy = CooldownPolicy(cooldown_seconds=300)
    now_ts = 1_712_710_600
    last_sent_status1 = now_ts - 60  # 60 seconds ago, still within cooldown for "2330+1"

    # Cooldown key "2330+1" is active – new "2330+1" should be blocked
    assert policy.can_send(last_sent_at=last_sent_status1, now_ts=now_ts) is False, (
        "[TP-POL-006a] 2330+1 sent 60s ago must still be blocked for status=1."
    )
    # Cooldown key "2330+2" has no history (last_sent_at=None) – should be sendable
    assert policy.can_send(last_sent_at=None, now_ts=now_ts) is True, (
        "[TP-POL-006a] 2330+2 with no history must be sendable even while 2330+1 is in cooldown."
    )


def test_tp_pol_006b_same_status1_different_method_blocked_within_cooldown():
    CooldownPolicy = require_symbol(
        "stock_monitor.domain.policies",
        "CooldownPolicy",
        "TP-POL-006b",
    )
    policy = CooldownPolicy(cooldown_seconds=300)
    now_ts = 1_712_710_600
    last_sent_status1 = now_ts - 60  # 60s ago, same stock_no+stock_status=1

    result = policy.can_send(last_sent_at=last_sent_status1, now_ts=now_ts)
    assert result is False, (
        "[TP-POL-006b] Same stock_no+status1 triggered by a different method must still be "
        "blocked within the 5-minute cooldown window."
    )
