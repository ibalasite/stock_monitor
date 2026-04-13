"""
BDD step definitions for TP-SEC-* and TP-ARCH-* scenarios.

These scenarios correspond to Code Review v0.8 improvement actions defined
in EDD §13.  Each step asserts the EXPECTED (fixed) behaviour.  Because the
CR-* fixes have not yet been implemented the assertions will FAIL, producing
the required RED signal.  After each fix is applied the corresponding tests
should turn GREEN without any changes to this file.
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import is_dataclass
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when


# ---------------------------------------------------------------------------
# Shared per-scenario context fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sec_arch_ctx() -> dict:
    """Mutable dict shared between GIVEN / WHEN / THEN steps of one scenario."""
    return {}


# ---------------------------------------------------------------------------
# TP-SEC-001  CR-SEC-01: token repr protection
# ---------------------------------------------------------------------------

@given(parsers.parse('LinePushClient 以 token "{token}" 初始化'))
def given_line_push_client_with_token(sec_arch_ctx, token):
    from stock_monitor.adapters.line_messaging import LinePushClient
    sec_arch_ctx["client"] = LinePushClient(channel_access_token=token, to_group_id="C1234567890")
    sec_arch_ctx["token"] = token


@when("對 LinePushClient 實例呼叫 repr()")
def when_call_repr_on_client(sec_arch_ctx):
    sec_arch_ctx["repr_output"] = repr(sec_arch_ctx["client"])


@then(parsers.parse('repr 輸出不應包含 "{text}"'))
def then_repr_should_not_contain(sec_arch_ctx, text):
    repr_output = sec_arch_ctx.get("repr_output", "")
    assert text not in repr_output, (
        f"[TP-SEC-001] repr() exposes token — CR-SEC-01 requires "
        f"field(repr=False) on channel_access_token. "
        f"Got repr output: {repr_output!r}"
    )


@then("LinePushClient 仍可正常發出 LINE API 請求")
def then_client_still_functional(sec_arch_ctx):
    # Verify the client object is well-formed (has required attributes);
    # we do NOT make a real network call here.
    client = sec_arch_ctx["client"]
    assert hasattr(client, "channel_access_token"), (
        "[TP-SEC-001] LinePushClient must retain channel_access_token attribute after repr fix"
    )
    assert hasattr(client, "send"), (
        "[TP-SEC-001] LinePushClient must still expose send() method"
    )


# ---------------------------------------------------------------------------
# TP-SEC-002  CR-SEC-03 / CR-CODE-05: timezone validation fail-fast
# ---------------------------------------------------------------------------

@given(parsers.parse('使用無效時區名稱 "{tz_name}"'))
def given_invalid_tz_name(sec_arch_ctx, tz_name):
    sec_arch_ctx["tz_name"] = tz_name
    sec_arch_ctx["raised_exc"] = None


@when("初始化 TimeBucketService 或呼叫 _resolve_timezone")
def when_init_with_invalid_tz(sec_arch_ctx):
    from stock_monitor.domain.time_bucket import TimeBucketService
    tz_name = sec_arch_ctx["tz_name"]
    try:
        sec_arch_ctx["service"] = TimeBucketService(tz_name)
    except ValueError as exc:
        sec_arch_ctx["raised_exc"] = exc
    except Exception as exc:
        # Any other exception type is also captured but will fail the THEN steps
        sec_arch_ctx["raised_exc"] = exc
        sec_arch_ctx["unexpected_exc_type"] = type(exc).__name__


@then("應立即 raise ValueError")
def then_should_raise_value_error(sec_arch_ctx):
    exc = sec_arch_ctx.get("raised_exc")
    unexpected_type = sec_arch_ctx.get("unexpected_exc_type")
    if unexpected_type:
        pytest.fail(
            f"[TP-SEC-002] Expected ValueError but got {unexpected_type}({exc}). "
            "CR-SEC-03 / CR-CODE-05: invalid timezone must raise ValueError immediately."
        )
    assert exc is not None, (
        f"[TP-SEC-002] TimeBucketService({sec_arch_ctx['tz_name']!r}) did NOT raise any exception. "
        "CR-SEC-03 / CR-CODE-05 require a ValueError fail-fast; currently silently falls back."
    )
    assert isinstance(exc, ValueError), (
        f"[TP-SEC-002] Raised {type(exc).__name__!r} instead of ValueError. "
        "CR-SEC-03 / CR-CODE-05 require ValueError."
    )


@then("不應繼續執行後續邏輯")
def then_should_not_continue(sec_arch_ctx):
    # If ValueError was NOT raised, a service object was created — that means
    # execution continued silently, which is the forbidden behaviour.
    assert sec_arch_ctx.get("raised_exc") is not None, (
        "[TP-SEC-002] After invalid timezone, execution must stop immediately (ValueError). "
        "Currently a service object is created with degraded _tz=None — CR-CODE-05 violation."
    )


@then("不應 fallback 至 UTC 時區")
def then_should_not_fallback_utc(sec_arch_ctx):
    # If a service was created (no exception), verify it didn't silently use UTC.
    # This step only passes if the exception WAS raised (i.e., the earlier steps pass).
    # We re-assert to give a clear message if it wasn't raised.
    assert sec_arch_ctx.get("raised_exc") is not None, (
        "[TP-SEC-002] No exception was raised — the service silently fell back to UTC. "
        "CR-SEC-03 requires raising ValueError instead of fallback to timezone.utc."
    )


# ---------------------------------------------------------------------------
# TP-ARCH-001  CR-ARCH-01/02 / CR-SEC-02: calculator in application layer
# ---------------------------------------------------------------------------

@given("stock_monitor.application.valuation_calculator 模組可 import")
def given_valuation_calculator_module_importable(sec_arch_ctx):
    # The GIVEN step just records intent; the THEN step does the real assertion.
    sec_arch_ctx["arch001_given"] = True


@when("執行一次估值計算（正常情境）")
def when_run_valuation_normal(sec_arch_ctx):
    # Attempt to run a minimal valuation using ManualValuationCalculator from application layer.
    try:
        from stock_monitor.application.valuation_calculator import ManualValuationCalculator
        sec_arch_ctx["calc_cls"] = ManualValuationCalculator
        sec_arch_ctx["calc_import_ok"] = True

        class _FakeRepo:
            def list_enabled(self_):
                return [{"stock_no": "2330", "manual_fair_price": 1500.0, "manual_cheap_price": 1000.0}]

        calc = ManualValuationCalculator(watchlist_repo=_FakeRepo(), trade_date="2026-04-14")
        sec_arch_ctx["calc_result"] = calc.calculate()
        sec_arch_ctx["calc_events"] = getattr(calc, "events", [])
    except ImportError as exc:
        sec_arch_ctx["calc_import_ok"] = False
        sec_arch_ctx["calc_import_error"] = str(exc)


@then("ManualValuationCalculator 應可從 application.valuation_calculator import")
def then_calculator_importable_from_application(sec_arch_ctx):
    if not sec_arch_ctx.get("calc_import_ok"):
        pytest.fail(
            "[TP-ARCH-001] CR-ARCH-01: "
            f"Cannot import ManualValuationCalculator from "
            f"stock_monitor.application.valuation_calculator — "
            f"{sec_arch_ctx.get('calc_import_error', 'unknown error')}. "
            "Move the class from app.py to application/valuation_calculator.py."
        )


@then("app.py 不應包含估值計算專屬 class 或 function 定義")
def then_apppy_no_calc_logic(sec_arch_ctx):
    import stock_monitor.app as app_module
    assert not hasattr(app_module, "_ManualValuationCalculator"), (
        "[TP-ARCH-001] CR-ARCH-01: app.py still defines _ManualValuationCalculator. "
        "After the fix, app.py must only import from application.valuation_calculator."
    )


@then("system_logs 不應出現 scenario_case 相關的偽造 skip 事件")
def then_no_fake_scenario_case_log(sec_arch_ctx):
    events = sec_arch_ctx.get("calc_events", [])
    fake = [e for e in events if "optional_indicator_v1" in str(e) and "SKIP_INSUFFICIENT_DATA" in str(e)]
    assert not fake, (
        f"[TP-ARCH-001] CR-SEC-02 / CR-ARCH-02: scenario_case='default' produces fake skip event(s): "
        f"{fake}. Remove the scenario_case production branch from valuation calculator."
    )


# ---------------------------------------------------------------------------
# TP-ARCH-002  CR-ARCH-03: single render definition
# ---------------------------------------------------------------------------

@given("已載入 stock_monitor.application.message_template")
def given_message_template_loaded(sec_arch_ctx):
    import stock_monitor.application.message_template as mt
    sec_arch_ctx["message_template_module"] = mt


@when('在整個專案中搜尋 "def render_line_template_message"')
def when_search_for_render_definition(sec_arch_ctx):
    import stock_monitor
    pkg_root = Path(inspect.getfile(stock_monitor)).parent
    definitions: list[str] = []
    for py_file in sorted(pkg_root.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "render_line_template_message":
                    definitions.append(py_file.name)
        except Exception:
            pass
    sec_arch_ctx["render_definitions"] = definitions


@then("只應在 message_template.py 中找到一個定義")
def then_only_one_render_definition(sec_arch_ctx):
    definitions = sec_arch_ctx.get("render_definitions", [])
    assert len(definitions) == 1, (
        f"[TP-ARCH-002] CR-ARCH-03: render_line_template_message defined in {len(definitions)} file(s): "
        f"{definitions}. Expected exactly 1 definition in message_template.py. "
        "Remove the duplicate in runtime_service.py and import from message_template instead."
    )


@then("runtime_service.py 不應包含 render_line_template_message 函式定義")
def then_runtime_service_no_render_definition(sec_arch_ctx):
    definitions = sec_arch_ctx.get("render_definitions", [])
    assert "runtime_service.py" not in definitions, (
        "[TP-ARCH-002] CR-ARCH-03: runtime_service.py still defines render_line_template_message. "
        "Replace the definition with an import from stock_monitor.application.message_template."
    )


# ---------------------------------------------------------------------------
# TP-ARCH-003  CR-CODE-03: MinuteCycleConfig dataclass
# ---------------------------------------------------------------------------

@given("stock_monitor.application.runtime_service 模組可 import")
def given_runtime_service_importable(sec_arch_ctx):
    import stock_monitor.application.runtime_service as rs
    sec_arch_ctx["runtime_service"] = rs


@when("從 runtime_service import MinuteCycleConfig")
def when_import_minute_cycle_config(sec_arch_ctx):
    rs = sec_arch_ctx["runtime_service"]
    try:
        sec_arch_ctx["MinuteCycleConfig"] = getattr(rs, "MinuteCycleConfig")
        sec_arch_ctx["minute_cycle_config_import_ok"] = True
    except AttributeError:
        sec_arch_ctx["minute_cycle_config_import_ok"] = False


@then("import 應成功")
def then_import_should_succeed(sec_arch_ctx):
    assert sec_arch_ctx.get("minute_cycle_config_import_ok"), (
        "[TP-ARCH-003] CR-CODE-03: MinuteCycleConfig not found in "
        "stock_monitor.application.runtime_service. "
        "Introduce a MinuteCycleConfig dataclass to replace the 12-parameter signature."
    )


@then("MinuteCycleConfig 應為 dataclass 或具名 config 型別")
def then_minute_cycle_config_is_dataclass(sec_arch_ctx):
    cls = sec_arch_ctx.get("MinuteCycleConfig")
    if cls is None:
        pytest.fail("[TP-ARCH-003] MinuteCycleConfig not imported — see previous step.")
    assert is_dataclass(cls), (
        f"[TP-ARCH-003] CR-CODE-03: MinuteCycleConfig ({cls!r}) is not a dataclass. "
        "It must be a dataclass or NamedTuple."
    )


@then("run_minute_cycle 應接受 MinuteCycleConfig 作為設定入口")
def then_run_minute_cycle_accepts_config(sec_arch_ctx):
    rs = sec_arch_ctx.get("runtime_service")
    if rs is None:
        pytest.fail("[TP-ARCH-003] runtime_service module not available.")
    sig = inspect.signature(rs.run_minute_cycle)
    param_names = list(sig.parameters.keys())
    # After fix the first positional/config param should be named "config" or "cfg"
    # OR the function accepts a MinuteCycleConfig; verify via type annotations or name.
    config_params = [p for p in param_names if p in {"config", "cfg", "minute_cycle_config"}]
    assert config_params, (
        f"[TP-ARCH-003] CR-CODE-03: run_minute_cycle still uses individual keyword params "
        f"({param_names}) instead of a MinuteCycleConfig config object. "
        "After the fix, the signature should accept a single config parameter."
    )


# ---------------------------------------------------------------------------
# TP-ARCH-004  CR-ARCH-06 / CR-CODE-06: DB-based opening summary idempotency
# ---------------------------------------------------------------------------

@given("系統採用 SqliteLogger 紀錄事件")
def given_sqlite_logger_used(sec_arch_ctx):
    from stock_monitor.adapters.sqlite_repo import SqliteLogger
    sec_arch_ctx["SqliteLogger"] = SqliteLogger


@when("查看 opening_summary_sent_for_date 的實作")
def when_inspect_opening_summary_sent_for_date(sec_arch_ctx):
    cls = sec_arch_ctx["SqliteLogger"]
    method = getattr(cls, "opening_summary_sent_for_date", None)
    sec_arch_ctx["method_exists"] = method is not None
    if method is not None:
        try:
            sec_arch_ctx["method_source"] = inspect.getsource(method)
        except Exception:
            sec_arch_ctx["method_source"] = ""


@then("不得使用 LIKE 查詢比對 system_logs.detail 判斷是否已發送")
def then_no_like_query_on_system_logs(sec_arch_ctx):
    source = sec_arch_ctx.get("method_source", "")
    assert "LIKE" not in source, (
        "[TP-ARCH-004] CR-ARCH-06: opening_summary_sent_for_date uses a LIKE query on "
        "system_logs.detail to determine if the opening summary was already sent today. "
        "This is the log-as-state anti-pattern. "
        "Replace with a dedicated DB column or table (e.g. opening_summary_sent_dates)."
    )


@then("應使用專屬 DB 狀態欄位或獨立資料表記錄已發送日期")
def then_uses_dedicated_db_state(sec_arch_ctx):
    source = sec_arch_ctx.get("method_source", "")
    # After fix, the method should query a dedicated table or column.
    # It must NOT use system_logs at all for this idempotency check.
    assert "system_logs" not in source, (
        "[TP-ARCH-004] CR-ARCH-06: opening_summary_sent_for_date still queries system_logs. "
        "After the fix, the method must query a dedicated idempotency store, "
        "not the general-purpose event log table."
    )
