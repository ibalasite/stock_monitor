from __future__ import annotations

from unittest.mock import patch

from ._contract import require_symbol


def test_tp_tpl_001_red_template_renderer_symbol_should_exist():
    require_symbol(
        "stock_monitor.application.message_template",
        "LineTemplateRenderer",
        "TP-TPL-001",
    )


def test_tp_tpl_002_red_runtime_template_hook_symbol_should_exist():
    require_symbol(
        "stock_monitor.application.runtime_service",
        "render_line_template_message",
        "TP-TPL-002",
    )


def test_tp_tpl_003_red_trigger_row_template_key_constant_must_exist():
    """TP-TPL-003 (contract): TRIGGER_ROW_TEMPLATE_KEY constant must be defined in
    runtime_service so build_minute_rows can reference it when calling render."""
    require_symbol(
        "stock_monitor.application.runtime_service",
        "TRIGGER_ROW_TEMPLATE_KEY",
        "TP-TPL-003",
    )


def test_tp_tpl_003_red_build_minute_rows_must_call_render_for_trigger_row():
    """TP-TPL-003 (behavioral): build_minute_rows must route each trigger row message
    through render_line_template_message(template_key, context) instead of assembling
    the final LINE text as a plain f-string.

    PDD FR-14: 業務層只能傳遞 template_key + context，不得直接拼接最終 LINE 文案。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import stock_monitor.application.runtime_service as rs

    class _FakeMsgRepo:
        def get_last_sent_at(self, _stock_no, _status):
            return None

    class _FakeRepo:
        pass

    now_dt = datetime(2026, 4, 14, 10, 0, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    hits = [
        {
            "stock_no": "2330",
            "stock_status": 2,
            "method": "manual_rule",
            "price": 980.0,
            "stock_name": "台積電",
            "fair_price": 1500.0,
            "cheap_price": 1000.0,
        }
    ]

    render_calls: list[str] = []

    original_render = rs.render_line_template_message

    def spy_render(template_key: str, context: dict) -> str:
        render_calls.append(template_key)
        return original_render(template_key, context)

    with patch.object(rs, "render_line_template_message", side_effect=spy_render):
        rs.build_minute_rows(
            now_dt=now_dt,
            hits=hits,
            message_repo=_FakeMsgRepo(),
            pending_repo=_FakeRepo(),
            pending_fallback=_FakeRepo(),
            cooldown_seconds=300,
        )

    trigger_calls = [k for k in render_calls if "trigger" in k.lower() or "row" in k.lower()]
    assert trigger_calls, (
        "[TP-TPL-003] build_minute_rows must call render_line_template_message with a "
        "trigger row template_key for each sendable event.\n"
        f"  Actual render calls observed: {render_calls!r}\n"
        "  Currently the trigger row message is assembled as a plain f-string. "
        "Introduce TRIGGER_ROW_TEMPLATE_KEY and route through render_line_template_message."
    )


def test_tp_tpl_003_red_minute_digest_template_key_constant_must_exist():
    """TP-TPL-003 (contract): MINUTE_DIGEST_TEMPLATE_KEY constant must be defined in
    monitoring_workflow so aggregate_minute_notifications can render the composite header
    and line entries via template instead of hardcoded strings."""
    require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "MINUTE_DIGEST_TEMPLATE_KEY",
        "TP-TPL-003",
    )


def test_tp_tpl_004_red_test_push_template_key_should_exist():
    """TP-TPL-004: A TEST_PUSH_TEMPLATE_KEY constant must be defined in runtime_service.
    PDD FR-14: 測試推播 / 營運驗證推播（若系統提供）也必須透過 template_key + context 渲染。
    """
    require_symbol(
        "stock_monitor.application.runtime_service",
        "TEST_PUSH_TEMPLATE_KEY",
        "TP-TPL-004",
    )


def test_tp_uat_014_red_trigger_row_template_key_must_exist():
    """TP-UAT-014 (partial): TRIGGER_ROW_TEMPLATE_KEY must be a named constant in
    runtime_service. Its presence is required so the template contract is enforceable
    and auditable without reading f-string source code."""
    require_symbol(
        "stock_monitor.application.runtime_service",
        "TRIGGER_ROW_TEMPLATE_KEY",
        "TP-UAT-014",
    )


def test_tp_uat_014_red_minute_digest_template_key_must_exist():
    """TP-UAT-014 (partial): MINUTE_DIGEST_TEMPLATE_KEY must be a named constant in
    monitoring_workflow. Its presence is required before aggregate_minute_notifications
    can be migrated to template rendering."""
    require_symbol(
        "stock_monitor.application.monitoring_workflow",
        "MINUTE_DIGEST_TEMPLATE_KEY",
        "TP-UAT-014",
    )


def test_tp_uat_014_red_aggregate_minute_notifications_must_call_render():
    """TP-UAT-014 (behavioral): aggregate_minute_notifications must produce the composite
    LINE header and entry lines via render_line_template_message, not via hardcoded
    '[股票監控通知]' strings or plain f-string concatenation.

    PDD FR-14 / EDD §2.7 / ADR-010:
    業務層只能傳遞 template_key + context，不得在程式中直接拼接最終 LINE 文案。
    """
    import stock_monitor.application.monitoring_workflow as mw
    import stock_monitor.application.runtime_service as rs

    rows = [
        {
            "stock_no": "2330",
            "stock_status": 2,
            "methods_hit": ["manual_rule"],
            "minute_bucket": "2026-04-14 10:00",
            "message": "台積電 low",
        }
    ]

    render_calls: list[str] = []

    def spy_render(template_key: str, context: dict) -> str:  # noqa: ARG001
        render_calls.append(template_key)
        return f"RENDERED:{template_key}"

    # Patch render in both modules so any import path is caught
    with (
        patch.object(rs, "render_line_template_message", side_effect=spy_render),
        patch("stock_monitor.application.monitoring_workflow.render_line_template_message", spy_render, create=True),
    ):
        mw.aggregate_minute_notifications("2026-04-14 10:00", rows)

    assert render_calls, (
        "[TP-UAT-014] aggregate_minute_notifications must call render_line_template_message "
        "for the composite LINE message (header + trigger lines).\n"
        "  Currently it hardcodes '[股票監控通知]' and uses plain string concatenation.\n"
        "  Introduce MINUTE_DIGEST_TEMPLATE_KEY and route through render_line_template_message."
    )

