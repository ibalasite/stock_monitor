"""
Unit tests for EDD §13 Code Review action items (v0.8).

Each test asserts the EXPECTED (fixed) behaviour described in EDD §13.
Because the CR-* fixes have NOT yet been implemented, every test in this
file will FAIL (RED phase of TDD).  After each fix is applied the
corresponding test should turn GREEN without any changes to this file.

Test IDs map to TEST_PLAN §6:
  TP-SEC-001  CR-SEC-01   — LinePushClient repr must not expose token
  TP-SEC-002  CR-SEC-03 / CR-CODE-05 — invalid timezone raises ValueError
  TP-SEC-003  CR-SEC-04   — HTTP response read must have a byte-size limit
  TP-ARCH-001 CR-ARCH-01/02 / CR-SEC-02 — calculator in application layer; no scenario_case
  TP-ARCH-002 CR-ARCH-03  — render_line_template_message has exactly one definition
  TP-ARCH-003 CR-CODE-03  — MinuteCycleConfig dataclass exists in runtime_service
  TP-ARCH-004 CR-ARCH-06  — opening_summary_sent_for_date uses DB not log-LIKE scan
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import is_dataclass
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# TP-SEC-001  CR-SEC-01: LinePushClient repr must not expose token
# ---------------------------------------------------------------------------


def test_tp_sec_001_line_push_client_repr_must_not_expose_token():
    """
    [TP-SEC-001] EDD §13.1 CR-SEC-01
    @dataclass LinePushClient without field(repr=False) includes the token in
    repr() output.  The fix is to annotate the field as field(repr=False).
    """
    from stock_monitor.adapters.line_messaging import LinePushClient

    client = LinePushClient(channel_access_token="secret_token_abc123", to_group_id="C1234567890")
    repr_output = repr(client)

    assert "secret_token_abc123" not in repr_output, (
        "[TP-SEC-001] repr() exposes channel_access_token in plaintext. "
        "CR-SEC-01 requires field(repr=False) on LinePushClient.channel_access_token. "
        f"Current repr: {repr_output!r}"
    )


# ---------------------------------------------------------------------------
# TP-SEC-002  CR-SEC-03 / CR-CODE-05: invalid timezone raises ValueError
# ---------------------------------------------------------------------------


def test_tp_sec_002_resolve_timezone_raises_on_invalid_name():
    """
    [TP-SEC-002] EDD §13.1 CR-SEC-03
    _resolve_timezone('Invalid/NotAZone') currently returns timezone.utc silently.
    The fix is to raise ValueError for any unrecognised timezone name.
    """
    from stock_monitor.app import _resolve_timezone

    with pytest.raises(ValueError, match=r"[Ii]nvalid"):
        _resolve_timezone("Invalid/NotAZone")



def test_tp_sec_002_time_bucket_service_raises_on_invalid_tz():
    """
    [TP-SEC-002] EDD §13.3 CR-CODE-05
    TimeBucketService.__init__ currently silently sets self._tz = None for
    unknown timezone names.  The fix is to raise ValueError immediately.
    """
    from stock_monitor.domain.time_bucket import TimeBucketService

    with pytest.raises(ValueError):
        TimeBucketService("Invalid/NotAZone")


# ---------------------------------------------------------------------------
# TP-SEC-003  CR-SEC-04: HTTP response read must enforce a byte-size limit
# ---------------------------------------------------------------------------


def test_tp_sec_003_market_data_http_read_enforces_size_limit(monkeypatch):
    """
    [TP-SEC-003] EDD §13.1 CR-SEC-04
    TwseRealtimeMarketDataProvider._http_get_json uses resp.read() without a
    byte-size argument.  The fix is to call resp.read(MAX_RESPONSE_BYTES)
    where MAX_RESPONSE_BYTES is defined as a module-level constant (default 1 MB).

    This test verifies that:
    1. MAX_RESPONSE_BYTES is defined in the market_data_twse module.
    2. resp.read() is called with a positional byte-size argument.
    """
    import stock_monitor.adapters.market_data_twse as m

    # (1) Constant must exist after the fix
    assert hasattr(m, "MAX_RESPONSE_BYTES"), (
        "[TP-SEC-003] MAX_RESPONSE_BYTES constant not defined in market_data_twse. "
        "CR-SEC-04 requires a module-level constant to cap response size."
    )

    # (2) resp.read() must be called with that limit
    read_call_args: list[tuple] = []

    class _TrackingResponse:
        def read(self, *args, **kwargs):
            read_call_args.append(args)
            return b'{"msgArray": []}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "stock_monitor.adapters.market_data_twse.request.urlopen",
        lambda *a, **kw: _TrackingResponse(),
    )
    from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider

    provider = TwseRealtimeMarketDataProvider()
    try:
        provider.get_realtime_quotes(["2330"])
    except Exception:
        pass  # other runtime errors are irrelevant here

    assert read_call_args, "[TP-SEC-003] resp.read() was never called"
    first_args = read_call_args[0]  # positional args tuple passed to read()
    assert first_args, (
        f"[TP-SEC-003] resp.read() called without a size limit (args={first_args}). "
        "CR-SEC-04 requires resp.read(MAX_RESPONSE_BYTES) to prevent memory exhaustion."
    )


# ---------------------------------------------------------------------------
# TP-ARCH-001  CR-ARCH-01/02 / CR-SEC-02
# ---------------------------------------------------------------------------


def test_tp_arch_001_calculator_importable_from_application_layer():
    """
    [TP-ARCH-001] EDD §13.2 CR-ARCH-01
    ManualValuationCalculator must live in stock_monitor.application.valuation_calculator.
    Currently it is defined inside app.py (Interface Layer) and the
    valuation_calculator module does not exist.
    """
    try:
        from stock_monitor.application.valuation_calculator import ManualValuationCalculator  # noqa: F401
    except ImportError as exc:
        pytest.fail(
            "[TP-ARCH-001] CR-ARCH-01: Cannot import ManualValuationCalculator from "
            f"stock_monitor.application.valuation_calculator — {exc}. "
            "Move the class from app.py to application/valuation_calculator.py."
        )



def test_tp_arch_001_app_py_must_not_define_calculator_class():
    """
    [TP-ARCH-001] EDD §13.2 CR-ARCH-01
    app.py (Interface Layer) must not contain any valuation calculator class.
    After the fix, app.py only imports ManualValuationCalculator from
    stock_monitor.application.valuation_calculator.
    """
    import stock_monitor.app as app_module

    assert not hasattr(app_module, "_ManualValuationCalculator"), (
        "[TP-ARCH-001] CR-ARCH-01: app.py still defines _ManualValuationCalculator. "
        "Move it to stock_monitor.application.valuation_calculator."
    )



def test_tp_arch_001_default_scenario_case_produces_no_fake_log_events():
    """
    [TP-ARCH-001] EDD §13.1 CR-SEC-02 / §13.2 CR-ARCH-02
    When scenario_case='default', the calculator emits a fake
    VALUATION_SKIP_INSUFFICIENT_DATA:optional_indicator_v1 log event on every
    call.  This is an artefact of the test-scenario branching code in the
    production path.  After the fix, no such event should appear.
    """
    from stock_monitor.app import _ManualValuationCalculator  # import from current (wrong) location

    class _FakeRepo:
        def list_enabled(self_):
            return [{"stock_no": "2330", "manual_fair_price": 1500.0, "manual_cheap_price": 1000.0}]

    calc = _ManualValuationCalculator(watchlist_repo=_FakeRepo(), trade_date="2026-04-14")
    calc.calculate()

    fake_events = [
        e for e in calc.events
        if "optional_indicator_v1" in str(e) and "SKIP_INSUFFICIENT_DATA" in str(e)
    ]
    assert not fake_events, (
        "[TP-ARCH-001] CR-SEC-02 / CR-ARCH-02: scenario_case='default' produced fake skip "
        f"event(s): {fake_events}. Remove the scenario_case production branch."
    )


# ---------------------------------------------------------------------------
# TP-ARCH-002  CR-ARCH-03: render_line_template_message has one definition
# ---------------------------------------------------------------------------


def test_tp_arch_002_render_function_has_single_definition_in_message_template():
    """
    [TP-ARCH-002] EDD §13.2 CR-ARCH-03
    render_line_template_message is currently defined in BOTH
    stock_monitor/application/message_template.py AND
    stock_monitor/application/runtime_service.py.
    After the fix, exactly one definition must exist (in message_template.py).
    runtime_service.py must import from message_template instead.
    """
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

    assert len(definitions) == 1, (
        f"[TP-ARCH-002] CR-ARCH-03: render_line_template_message is defined in "
        f"{len(definitions)} file(s): {definitions}. "
        "Expected exactly 1 definition in message_template.py. "
        "Remove the duplicate from runtime_service.py."
    )
    assert definitions == ["message_template.py"], (
        f"[TP-ARCH-002] CR-ARCH-03: sole definition must be in message_template.py, "
        f"found in: {definitions}"
    )


# ---------------------------------------------------------------------------
# TP-ARCH-003  CR-CODE-03: MinuteCycleConfig dataclass must exist
# ---------------------------------------------------------------------------


def test_tp_arch_003_minute_cycle_config_importable_from_runtime_service():
    """
    [TP-ARCH-003] EDD §13.3 CR-CODE-03
    run_minute_cycle has 12 keyword-only parameters.  The fix introduces a
    MinuteCycleConfig dataclass that bundles all config fields so call-sites
    pass a single object.  Currently MinuteCycleConfig does not exist.
    """
    try:
        from stock_monitor.application.runtime_service import MinuteCycleConfig  # noqa: F401
    except ImportError as exc:
        pytest.fail(
            f"[TP-ARCH-003] CR-CODE-03: MinuteCycleConfig not importable — {exc}. "
            "Add a MinuteCycleConfig dataclass to runtime_service.py."
        )



def test_tp_arch_003_minute_cycle_config_is_dataclass():
    """
    [TP-ARCH-003] EDD §13.3 CR-CODE-03
    Once MinuteCycleConfig exists it must be a proper dataclass (not a plain dict or class).
    """
    try:
        from stock_monitor.application.runtime_service import MinuteCycleConfig
    except ImportError:
        pytest.fail("[TP-ARCH-003] MinuteCycleConfig not importable — see previous test.")

    assert is_dataclass(MinuteCycleConfig), (
        f"[TP-ARCH-003] CR-CODE-03: MinuteCycleConfig ({MinuteCycleConfig!r}) is not a dataclass."
    )



def test_tp_arch_003_run_minute_cycle_accepts_config_object():
    """
    [TP-ARCH-003] EDD §13.3 CR-CODE-03
    After the fix, run_minute_cycle should accept a single 'config' (or 'cfg')
    parameter instead of 12 individual keyword arguments.
    """
    from stock_monitor.application.runtime_service import run_minute_cycle

    sig = inspect.signature(run_minute_cycle)
    param_names = list(sig.parameters.keys())
    config_params = [p for p in param_names if p in {"config", "cfg", "minute_cycle_config"}]

    assert config_params, (
        f"[TP-ARCH-003] CR-CODE-03: run_minute_cycle still has individual params {param_names}. "
        "After fix, the first parameter should be 'config: MinuteCycleConfig'."
    )


# ---------------------------------------------------------------------------
# TP-ARCH-004  CR-ARCH-06: opening_summary_sent_for_date must NOT use LIKE
# ---------------------------------------------------------------------------


def test_tp_arch_004_opening_summary_idempotency_must_not_use_like_on_system_logs():
    """
    [TP-ARCH-004] EDD §13.2 CR-ARCH-06
    SqliteLogger.opening_summary_sent_for_date currently determines whether the
    opening summary was already sent today by running:

        SELECT 1 FROM system_logs WHERE event = 'OPENING_SUMMARY_SENT'
        AND detail LIKE '%date=YYYY-MM-DD%'

    This is the log-as-state anti-pattern.  If the daemon restarts, the in-memory
    knowledge is gone — but the query still works (fragile).  The correct fix is a
    dedicated DB column or table that stores the sent date:

        e.g.  opening_summary_sent_dates(trade_date TEXT PRIMARY KEY)

    After the fix, LIKE must NOT appear in this method's SQL.
    """
    from stock_monitor.adapters.sqlite_repo import SqliteLogger

    source = inspect.getsource(SqliteLogger.opening_summary_sent_for_date)

    assert "LIKE" not in source, (
        "[TP-ARCH-004] CR-ARCH-06: opening_summary_sent_for_date uses a LIKE query on "
        "system_logs.detail (log-as-state anti-pattern). "
        "Replace with a dedicated DB idempotency store."
    )



def test_tp_arch_004_opening_summary_idempotency_must_not_query_system_logs():
    """
    [TP-ARCH-004] EDD §13.2 CR-ARCH-06
    After the fix, opening_summary_sent_for_date must not touch system_logs at all
    for idempotency checks.
    """
    from stock_monitor.adapters.sqlite_repo import SqliteLogger

    source = inspect.getsource(SqliteLogger.opening_summary_sent_for_date)

    assert "system_logs" not in source, (
        "[TP-ARCH-004] CR-ARCH-06: opening_summary_sent_for_date still queries system_logs. "
        "Use a dedicated table (e.g. opening_summary_sent_dates) instead."
    )
