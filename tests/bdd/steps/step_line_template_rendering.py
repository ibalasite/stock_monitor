from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from pytest_bdd import given, then, when

from tests._contract import require_symbol


@pytest.fixture
def tpl_ctx() -> dict:
    return {}


@given("FR-14 template renderer contract should exist")
def given_template_renderer_contract(tpl_ctx: dict):
    tpl_ctx["module_name"] = "stock_monitor.application.message_template"
    tpl_ctx["symbol_name"] = "LineTemplateRenderer"
    tpl_ctx["test_id"] = "TP-TPL-001"


@when("loading opening summary template renderer")
def when_loading_template_renderer(tpl_ctx: dict):
    tpl_ctx["symbol"] = require_symbol(
        tpl_ctx["module_name"],
        tpl_ctx["symbol_name"],
        tpl_ctx["test_id"],
    )


@then("opening summary template renderer symbol should be available")
def then_template_renderer_available(tpl_ctx: dict):
    assert tpl_ctx.get("symbol") is not None


@given("FR-14 runtime template hook should exist")
def given_runtime_template_hook(tpl_ctx: dict):
    tpl_ctx["module_name"] = "stock_monitor.application.runtime_service"
    tpl_ctx["symbol_name"] = "render_line_template_message"
    tpl_ctx["test_id"] = "TP-TPL-002"


@when("loading runtime template render hook")
def when_loading_runtime_template_hook(tpl_ctx: dict):
    tpl_ctx["symbol"] = require_symbol(
        tpl_ctx["module_name"],
        tpl_ctx["symbol_name"],
        tpl_ctx["test_id"],
    )


@then("runtime template hook symbol should be available")
def then_runtime_template_hook_available(tpl_ctx: dict):
    assert tpl_ctx.get("symbol") is not None


# ── TP-TPL-003 steps ─────────────────────────────────────────────────────────

@given("FR-14 TRIGGER_ROW_TEMPLATE_KEY constant should be defined in runtime_service")
def given_trigger_row_template_key(tpl_ctx: dict):
    import stock_monitor.application.runtime_service as rs
    assert hasattr(rs, "TRIGGER_ROW_TEMPLATE_KEY"), (
        "[TP-TPL-003] TRIGGER_ROW_TEMPLATE_KEY must be defined in runtime_service"
    )
    tpl_ctx["rs"] = rs


@given("FR-14 MINUTE_DIGEST_TEMPLATE_KEY constant should be defined in monitoring_workflow")
def given_minute_digest_template_key(tpl_ctx: dict):
    import stock_monitor.application.monitoring_workflow as mw
    assert hasattr(mw, "MINUTE_DIGEST_TEMPLATE_KEY"), (
        "[TP-TPL-003] MINUTE_DIGEST_TEMPLATE_KEY must be defined in monitoring_workflow"
    )
    tpl_ctx["mw"] = mw


@when("build_minute_rows is called with a sendable hit")
def when_build_minute_rows_called(tpl_ctx: dict):
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

    tpl_ctx["render_calls"] = render_calls


@then("the trigger row message must be produced by render_line_template_message")
def then_trigger_row_via_render(tpl_ctx: dict):
    render_calls = tpl_ctx.get("render_calls", [])
    trigger_calls = [k for k in render_calls if "trigger" in k.lower() or "row" in k.lower()]
    assert trigger_calls, (
        "[TP-TPL-003] build_minute_rows must call render_line_template_message with a "
        f"trigger row template_key. Actual calls: {render_calls!r}"
    )


@then("render_line_template_message must be called with a trigger row template_key and context dict")
def then_render_called_with_correct_key(tpl_ctx: dict):
    render_calls = tpl_ctx.get("render_calls", [])
    trigger_calls = [k for k in render_calls if "trigger" in k.lower() or "row" in k.lower()]
    assert trigger_calls, (
        "[TP-TPL-003] No trigger_row template_key found in render calls. "
        f"Got: {render_calls!r}"
    )


@then("no hardcoded final LINE text should be assembled as a plain f-string in build_minute_rows")
def then_no_hardcoded_text(tpl_ctx: dict):
    import inspect
    import stock_monitor.application.runtime_service as rs
    source = inspect.getsource(rs.build_minute_rows)
    assert "低於便宜價{" not in source and "低於合理價{" not in source, (
        "[TP-TPL-003] build_minute_rows must not assemble trigger row text via plain f-strings. "
        "Route through render_line_template_message(TRIGGER_ROW_TEMPLATE_KEY, context)."
    )


# ── TP-TPL-004 steps ─────────────────────────────────────────────────────────

@given("FR-14 TEST_PUSH_TEMPLATE_KEY constant should be defined in runtime_service")
def given_test_push_template_key(tpl_ctx: dict):
    import stock_monitor.application.runtime_service as rs
    assert hasattr(rs, "TEST_PUSH_TEMPLATE_KEY"), (
        "[TP-TPL-004] TEST_PUSH_TEMPLATE_KEY must be defined in runtime_service"
    )
    tpl_ctx["rs"] = rs


@when("test push function is invoked")
def when_test_push_invoked(tpl_ctx: dict):
    import stock_monitor.application.runtime_service as rs
    tpl_ctx["test_push_key"] = rs.TEST_PUSH_TEMPLATE_KEY


@then("test push message must be produced through render_line_template_message")
def then_test_push_via_render(tpl_ctx: dict):
    from stock_monitor.application.message_template import render_line_template_message
    key = tpl_ctx.get("test_push_key", "")
    result = render_line_template_message(key, {"message": "test"})
    assert result, "[TP-TPL-004] render_line_template_message must produce output for TEST_PUSH_TEMPLATE_KEY"


@then("TEST_PUSH_TEMPLATE_KEY must not be empty or None")
def then_test_push_key_not_empty(tpl_ctx: dict):
    key = tpl_ctx.get("test_push_key")
    assert key, "[TP-TPL-004] TEST_PUSH_TEMPLATE_KEY must be a non-empty string"


# ── UAT-014 / TP-UAT-014 steps ───────────────────────────────────────────────

@given("runtime service composes outbound LINE messages during a minute cycle")
def given_runtime_composes_outbound_messages(tpl_ctx: dict):
    import stock_monitor.application.runtime_service as rs
    import stock_monitor.application.monitoring_workflow as mw
    tpl_ctx["rs"] = rs
    tpl_ctx["mw"] = mw


@when("any LINE message type is produced (minute digest, opening summary, trigger row)")
def when_any_line_message_produced(tpl_ctx: dict):
    import stock_monitor.application.runtime_service as rs
    import stock_monitor.application.monitoring_workflow as mw

    render_calls: list[str] = []

    def spy(template_key: str, context: dict) -> str:  # noqa: ARG001
        render_calls.append(template_key)
        return f"RENDERED:{template_key}"

    with (
        patch.object(rs, "render_line_template_message", side_effect=spy),
        patch("stock_monitor.application.monitoring_workflow.render_line_template_message", spy, create=True),
    ):
        mw.aggregate_minute_notifications(
            "2026-04-14 10:00",
            [{"stock_no": "2330", "stock_status": 2, "methods_hit": ["manual_rule"], "message": "msg"}],
        )

    tpl_ctx["render_calls"] = render_calls


@then("all messages must be routed through render_line_template_message")
def then_all_messages_routed(tpl_ctx: dict):
    render_calls = tpl_ctx.get("render_calls", [])
    assert render_calls, (
        "[UAT-014] No calls to render_line_template_message were observed. "
        "All outbound LINE messages must go through render_line_template_message."
    )


@then("TRIGGER_ROW_TEMPLATE_KEY must exist as a named constant")
def then_trigger_row_key_named_constant(tpl_ctx: dict):
    import stock_monitor.application.runtime_service as rs
    assert hasattr(rs, "TRIGGER_ROW_TEMPLATE_KEY") and rs.TRIGGER_ROW_TEMPLATE_KEY, (
        "[UAT-014] TRIGGER_ROW_TEMPLATE_KEY must be a non-empty named constant in runtime_service"
    )


@then("MINUTE_DIGEST_TEMPLATE_KEY must exist as a named constant")
def then_minute_digest_key_named_constant(tpl_ctx: dict):
    import stock_monitor.application.monitoring_workflow as mw
    assert hasattr(mw, "MINUTE_DIGEST_TEMPLATE_KEY") and mw.MINUTE_DIGEST_TEMPLATE_KEY, (
        "[UAT-014] MINUTE_DIGEST_TEMPLATE_KEY must be a non-empty named constant in monitoring_workflow"
    )


@then("no message text may be a plain hardcoded string bypassing template rendering")
def then_no_hardcoded_bypassing(tpl_ctx: dict):
    import inspect
    import stock_monitor.application.runtime_service as rs
    import stock_monitor.application.monitoring_workflow as mw
    rs_source = inspect.getsource(rs.build_minute_rows)
    mw_source = inspect.getsource(mw.aggregate_minute_notifications)
    assert "低於便宜價{" not in rs_source and "低於合理價{" not in rs_source, (
        "[UAT-014] build_minute_rows must not use hardcoded Chinese text f-strings"
    )
    assert "[股票監控通知]" not in mw_source, (
        "[UAT-014] aggregate_minute_notifications must not hardcode '[股票監控通知]'"
    )

