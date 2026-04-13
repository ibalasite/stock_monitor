"""
Unit / integration RED tests for EDD §13 Code Review action items (v0.8) — Phase 2.

Tests cover the remaining un-implemented / partially-implemented CR items:

  TP-ARCH-005  CR-ARCH-04  — app.py must only contain entry-point + command routing
  TP-ARCH-006  CR-ARCH-05  — merge_minute_message must have a production caller or be private
  TP-CODE-001  CR-CODE-01  — build_minute_rows must unify 3 near-identical render calls to 1
  TP-CODE-002  CR-CODE-02  — reconcile_pending_once must remove the unused line_client param
  TP-CODE-003  CR-CODE-04  — aggregate_minute_notifications must use template render, not f-string
  TP-CODE-004  CR-CODE-06  — opening summary must trigger on any trading minute, not just 09:00

Every test in this file is expected to FAIL (RED) until the corresponding production fix
is applied.  After each fix the test should turn GREEN with zero changes to this file.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ast_fn_names_in_file(path: Path) -> list[str]:
    """Return all top-level and nested function/method names defined in *path*."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    return [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _count_render_calls_in_source(src: str) -> int:
    """Count calls to render_line_template_message in the given source snippet."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "render_line_template_message":
                count += 1
            elif isinstance(func, ast.Attribute) and func.attr == "render_line_template_message":
                count += 1
    return count


# ---------------------------------------------------------------------------
# TP-ARCH-005  CR-ARCH-04 — app.py must NOT define daemon loop / DI assembly
# ---------------------------------------------------------------------------

class TestTpArch005AppPySrp:
    """
    [TP-ARCH-005] EDD §13.2 CR-ARCH-04

    app.py (Interface Layer) currently contains:
    - _run_daemon_loop : daemon business logic (~80 lines)
    - _build_runtime   : dependency-injection assembly

    After the fix, these must reside in a dedicated service/factory module
    (e.g. stock_monitor/application/daemon_runner.py or runtime_service.py).
    app.py should only define: _build_parser, main, and thin command handlers.
    """

    def test_app_py_must_not_define_run_daemon_loop(self):
        """
        [TP-ARCH-005a] _run_daemon_loop is daemon logic; it must not live in app.py.
        """
        import stock_monitor.app as _app_module
        app_path = Path(_app_module.__file__)
        defined_names = _ast_fn_names_in_file(app_path)

        assert "_run_daemon_loop" not in defined_names, (
            "[TP-ARCH-005a] CR-ARCH-04: app.py still defines _run_daemon_loop. "
            "Move daemon loop logic to stock_monitor/application/ and import it in app.py."
        )

    def test_app_py_must_not_define_build_runtime(self):
        """
        [TP-ARCH-005b] _build_runtime performs DI assembly; it must not live in app.py.
        """
        import stock_monitor.app as _app_module
        app_path = Path(_app_module.__file__)
        defined_names = _ast_fn_names_in_file(app_path)

        assert "_build_runtime" not in defined_names, (
            "[TP-ARCH-005b] CR-ARCH-04: app.py still defines _build_runtime. "
            "Move DI assembly to a factory/service module and import it in app.py."
        )

    def test_app_py_only_exposes_expected_public_symbols(self):
        """
        [TP-ARCH-005c] After SRP split, the only *user-facing* callables in app.py
        should be main() and _build_parser().  Helper functions for business logic
        must be moved to application layer.
        """
        import stock_monitor.app as _app_module
        app_path = Path(_app_module.__file__)
        all_fns = _ast_fn_names_in_file(app_path)

        # These must NOT be in app.py after the fix
        prohibited = {"_run_daemon_loop", "_build_runtime"}
        found = prohibited.intersection(all_fns)

        assert not found, (
            f"[TP-ARCH-005c] CR-ARCH-04: app.py still defines prohibited functions: {sorted(found)}. "
            "app.py must only contain entry-point bootstrapping and command routing."
        )


# ---------------------------------------------------------------------------
# TP-ARCH-006  CR-ARCH-05 — merge_minute_message needs caller or must be private
# ---------------------------------------------------------------------------

class TestTpArch006MergeMinuteMessageVisibility:
    """
    [TP-ARCH-006] EDD §13.2 CR-ARCH-05

    monitoring_workflow.py exports merge_minute_message as a public symbol, but
    zero production code paths call it — only tests do.  According to EDD §13.2:

    "若僅作為測試輔助，應標記私有（_merge_minute_message）或移入測試層；
     若為正式 API 需補充真實呼叫點"

    The fix must be one of:
    a) Rename to _merge_minute_message (private convention)
    b) Add a real production call site inside the package
    """

    def test_merge_minute_message_has_production_caller_or_is_private(self):
        """
        [TP-ARCH-006] merge_minute_message must not remain a public orphan.
        Either rename _merge_minute_message OR add a production call site.
        """
        import stock_monitor
        from stock_monitor.application import monitoring_workflow as wf

        # If already renamed to private – passes immediately
        if not hasattr(wf, "merge_minute_message"):
            return  # private rename done → GREEN

        # Still public → must have at least one production call site
        pkg_root = Path(inspect.getfile(stock_monitor)).parent
        production_callers: list[str] = []

        for py_file in sorted(pkg_root.rglob("*.py")):
            # Skip test artefacts
            rel = py_file.relative_to(pkg_root.parent)
            if any(part.startswith("test") for part in rel.parts):
                continue
            try:
                src = py_file.read_text(encoding="utf-8")
                tree = ast.parse(src, filename=str(py_file))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if isinstance(func, ast.Name) and func.id == "merge_minute_message":
                        production_callers.append(str(py_file.name))
                    elif isinstance(func, ast.Attribute) and func.attr == "merge_minute_message":
                        production_callers.append(str(py_file.name))
            except Exception:
                pass

        assert production_callers, (
            "[TP-ARCH-006] CR-ARCH-05: merge_minute_message is public but has no production "
            "callers inside the stock_monitor package. "
            "Either rename it to _merge_minute_message or add a real production call site."
        )


# ---------------------------------------------------------------------------
# TP-CODE-001  CR-CODE-01 — build_minute_rows: single render call per row
# ---------------------------------------------------------------------------

class TestTpCode001BuildMinuteRowsSingleRender:
    """
    [TP-CODE-001] EDD §13.3 CR-CODE-01

    build_minute_rows in runtime_service.py contains three nearly-identical
    render_line_template_message calls inside an if/elif/else block:

        if status == 2 and cheap_price is not None:
            message = render_line_template_message(TRIGGER_ROW_TEMPLATE_KEY, {...})
        elif fair_price is not None:
            message = render_line_template_message(TRIGGER_ROW_TEMPLATE_KEY, {...})
        else:
            message = render_line_template_message(TRIGGER_ROW_TEMPLATE_KEY, {...})

    The fix should unify these into a single render call with a pre-built context dict,
    reducing duplication and making future template changes touch one place.
    """

    def test_build_minute_rows_has_at_most_one_render_call(self):
        """
        [TP-CODE-001] build_minute_rows must contain ≤ 1 render_line_template_message call.
        """
        from stock_monitor.application import runtime_service as rs

        src = inspect.getsource(rs.build_minute_rows)
        call_count = _count_render_calls_in_source(src)

        assert call_count <= 1, (
            f"[TP-CODE-001] CR-CODE-01: build_minute_rows contains {call_count} "
            "render_line_template_message calls. "
            "Unify to a single call with a pre-assembled context dict."
        )

    def test_build_minute_rows_no_duplicate_context_keys(self):
        """
        [TP-CODE-001] Complementary: build_minute_rows must not construct the same
        template context keys in multiple branches (sign of copy-paste duplication).
        """
        from stock_monitor.application import runtime_service as rs

        src = inspect.getsource(rs.build_minute_rows)
        # Count occurrences of 'display_label' as context key — a proxy for branch duplication
        # Each branch currently builds {"display_label": ..., "current_price": ..., ...}
        occurrences = src.count('"display_label"')
        assert occurrences <= 1, (
            f"[TP-CODE-001] CR-CODE-01: 'display_label' context key defined {occurrences} "
            "times in build_minute_rows. Consolidate into a single context dict."
        )


# ---------------------------------------------------------------------------
# TP-CODE-002  CR-CODE-02 — reconcile_pending_once: remove unused line_client param
# ---------------------------------------------------------------------------

class TestTpCode002ReconcilePendingUnusedParam:
    """
    [TP-CODE-002] EDD §13.3 CR-CODE-02

    reconcile_pending_once currently accepts line_client but immediately discards it:

        def reconcile_pending_once(line_client, message_repo, pending_repo, logger):
            ...
            _ = line_client   # ← never actually used

    The comment in the source says "Reconcile must only backfill DB state and never
    re-send LINE."  If LINE is never re-sent, the parameter is permanently dead weight.

    The fix: remove line_client from the function signature (and all call sites).
    """

    def test_reconcile_pending_once_signature_has_no_line_client(self):
        """
        [TP-CODE-002a] reconcile_pending_once must not declare a line_client parameter.
        """
        from stock_monitor.application.monitoring_workflow import reconcile_pending_once

        sig = inspect.signature(reconcile_pending_once)
        assert "line_client" not in sig.parameters, (
            "[TP-CODE-002a] CR-CODE-02: reconcile_pending_once still declares 'line_client' "
            "parameter, which is unused (body: _ = line_client). "
            "Remove the parameter and update all call sites."
        )

    def test_reconcile_pending_once_source_has_no_discard_pattern(self):
        """
        [TP-CODE-002b] The '_ = line_client' discard pattern must not exist in the source.
        """
        from stock_monitor.application import monitoring_workflow as wf

        src = inspect.getsource(wf.reconcile_pending_once)
        assert "_ = line_client" not in src, (
            "[TP-CODE-002b] CR-CODE-02: '_ = line_client' discard pattern still present. "
            "Remove the parameter so the discard is no longer needed."
        )

    def test_run_reconcile_cycle_does_not_pass_line_client_to_inner_fn(self):
        """
        [TP-CODE-002c] The wrapper run_reconcile_cycle must also stop forwarding
        line_client to reconcile_pending_once.
        """
        from stock_monitor.application import runtime_service as rs

        src = inspect.getsource(rs.run_reconcile_cycle)
        # After fix, reconcile_pending_once is called without line_client=
        assert "line_client=line_client" not in src, (
            "[TP-CODE-002c] CR-CODE-02: run_reconcile_cycle still forwards line_client= to "
            "reconcile_pending_once. Remove once the inner function no longer accepts it."
        )


# ---------------------------------------------------------------------------
# TP-CODE-003  CR-CODE-04 — aggregate_minute_notifications: no hand-rolled f-string rows
# ---------------------------------------------------------------------------

class TestTpCode003AggregateNotificationsTemplateRender:
    """
    [TP-CODE-003] EDD §13.3 CR-CODE-04

    aggregate_minute_notifications in monitoring_workflow.py currently generates
    the per-stock trigger row with a raw f-string:

        lines.append(f"{idx}) {base_message}（命中方法: {methods}）")
        # or
        lines.append(f"{idx}) {base_message}")

    FR-14 / EDD §2.7 mandate that all LINE outbound text be produced by the
    template renderer.  The fix replaces the f-string with:

        render_line_template_message(TRIGGER_ROW_TEMPLATE_KEY, context)
    """

    def test_aggregate_notifications_has_no_fstring_row_assembly(self):
        """
        [TP-CODE-003a] aggregate_minute_notifications must not use f-strings to assemble
        trigger row text.
        """
        from stock_monitor.application import monitoring_workflow as wf

        src = inspect.getsource(wf.aggregate_minute_notifications)
        # Both branches currently produce f"{idx}) ..." strings
        assert 'f"{idx})' not in src, (
            "[TP-CODE-003a] CR-CODE-04: aggregate_minute_notifications uses raw f-string "
            "to build trigger rows (f\"{idx}) ...\"). "
            "Replace with render_line_template_message(TRIGGER_ROW_TEMPLATE_KEY, context)."
        )

    def test_aggregate_notifications_calls_render_for_each_row(self):
        """
        [TP-CODE-003b] After the fix, aggregate_minute_notifications must call
        render_line_template_message for both the header and each signal row.
        With N signals there should be N+1 render calls (1 header + N rows).
        Verify with a 2-signal input → 3 render calls.
        """
        from unittest.mock import patch, call
        from stock_monitor.application.monitoring_workflow import aggregate_minute_notifications

        signals = [
            {"stock_no": "2330", "message": "2330 低於合理價", "methods_hit": ["emily_composite_v1"]},
            {"stock_no": "2317", "message": "2317 低於便宜價", "methods_hit": ["oldbull_dividend_yield_v1"]},
        ]

        render_calls: list[tuple] = []

        def _mock_render(template_key, context):
            render_calls.append((template_key, context))
            return f"RENDERED:{template_key}"

        with patch(
            "stock_monitor.application.monitoring_workflow.render_line_template_message",
            side_effect=_mock_render,
        ):
            aggregate_minute_notifications("2026-04-14 09:00", signals)

        # Expect: 1 header call + 1 call per signal = 3 total
        assert len(render_calls) >= 3, (
            f"[TP-CODE-003b] CR-CODE-04: expected ≥3 render calls (1 header + 2 rows), "
            f"got {len(render_calls)}. Each signal row must use render_line_template_message."
        )


# ---------------------------------------------------------------------------
# TP-CODE-004  CR-CODE-06 — opening summary must not require exact 09:00
# ---------------------------------------------------------------------------

class TestTpCode004OpeningSummaryFlexibleTrigger:
    """
    [TP-CODE-004] EDD §13.3 CR-CODE-06 / §13.2 CR-ARCH-04 (partial)

    _send_opening_summary_if_needed in runtime_service.py contains:

        if now_dt.strftime("%H:%M") != "09:00":
            return

    This means if the daemon starts at 09:01 or later, the opening summary
    for that trading day is permanently skipped — violating the requirement:

    EDD §13.3 CR-CODE-06:
    "觸發條件改為「交易日當日第一個尚未發送開盤摘要的分鐘」，允許 09:00 後 restart 觸發補送"

    The fix: replace the exact-time guard with a check against the DB idempotency
    record; if today's opening summary has NOT been sent regardless of current time,
    trigger the summary.
    """

    def _make_logger(self, *, already_sent: bool = False):
        sent_dates: list[str] = []

        class _Logger:
            def log(self, level, event):
                pass

            def opening_summary_sent_for_date(self, trade_date: str) -> bool:
                return already_sent

            def mark_opening_summary_sent(self, trade_date: str) -> None:
                sent_dates.append(trade_date)

        return _Logger(), sent_dates

    def test_opening_summary_fires_at_0901_when_not_sent(self):
        """
        [TP-CODE-004a] At 09:01, if the DB shows today's summary NOT sent, it must fire.
        Currently blocked by the hard-coded '09:00' guard → test is RED.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from stock_monitor.application.runtime_service import _send_opening_summary_if_needed

        sent_payloads: list[str] = []

        class _FakeLineClient:
            def send(self, msg):
                sent_payloads.append(msg)

        logger, _ = self._make_logger(already_sent=False)
        tz = ZoneInfo("Asia/Taipei")
        now_dt = datetime(2026, 4, 14, 9, 1, 0, tzinfo=tz)  # 09:01 — NOT 09:00

        _send_opening_summary_if_needed(
            now_dt=now_dt,
            watchlist_rows=[{"stock_no": "2330", "manual_fair_price": 1500.0, "manual_cheap_price": 1000.0}],
            valuation_snapshot_repo=None,
            line_client=_FakeLineClient(),
            logger=logger,
        )

        assert sent_payloads, (
            "[TP-CODE-004a] CR-CODE-06: _send_opening_summary_if_needed did NOT send at 09:01 "
            "even though today's summary has not been sent. "
            "Remove the hard-coded '09:00' time guard; check DB idempotency record instead."
        )

    def test_opening_summary_skipped_at_0901_when_already_sent(self):
        """
        [TP-CODE-004b] At 09:01, if the DB shows today's summary ALREADY sent, skip it.
        This test will pass once CR-CODE-06 is fixed (DB-first logic).
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from stock_monitor.application.runtime_service import _send_opening_summary_if_needed

        sent_payloads: list[str] = []

        class _FakeLineClient:
            def send(self, msg):
                sent_payloads.append(msg)

        logger, _ = self._make_logger(already_sent=True)
        tz = ZoneInfo("Asia/Taipei")
        now_dt = datetime(2026, 4, 14, 9, 1, 0, tzinfo=tz)

        _send_opening_summary_if_needed(
            now_dt=now_dt,
            watchlist_rows=[{"stock_no": "2330", "manual_fair_price": 1500.0, "manual_cheap_price": 1000.0}],
            valuation_snapshot_repo=None,
            line_client=_FakeLineClient(),
            logger=logger,
        )

        assert not sent_payloads, (
            "[TP-CODE-004b] CR-CODE-06: _send_opening_summary_if_needed sent at 09:01 "
            "even though today's summary was already recorded as sent in DB."
        )

    def test_opening_summary_fires_at_1005_on_late_restart(self):
        """
        [TP-CODE-004c] Edge case: daemon restarts at 10:05 (still in trading session).
        If today's opening summary has never been sent, it must fire at 10:05.
        The hard-coded '09:00' guard permanently prevents this → RED.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from stock_monitor.application.runtime_service import _send_opening_summary_if_needed

        sent_payloads: list[str] = []

        class _FakeLineClient:
            def send(self, msg):
                sent_payloads.append(msg)

        logger, _ = self._make_logger(already_sent=False)
        tz = ZoneInfo("Asia/Taipei")
        now_dt = datetime(2026, 4, 14, 10, 5, 0, tzinfo=tz)  # 10:05

        _send_opening_summary_if_needed(
            now_dt=now_dt,
            watchlist_rows=[{"stock_no": "2330", "manual_fair_price": 1500.0, "manual_cheap_price": 1000.0}],
            valuation_snapshot_repo=None,
            line_client=_FakeLineClient(),
            logger=logger,
        )

        assert sent_payloads, (
            "[TP-CODE-004c] CR-CODE-06: _send_opening_summary_if_needed did NOT send at 10:05 "
            "even though today's summary has never been sent (daemon late-restart scenario). "
            "Replace exact '09:00' guard with DB-idempotency-based check."
        )

    def test_opening_summary_source_has_no_exact_0900_guard(self):
        """
        [TP-CODE-004d] Static check: the source of _send_opening_summary_if_needed
        must not contain the literal '09:00' time-guard string comparison.
        """
        from stock_monitor.application import runtime_service as rs

        src = inspect.getsource(rs._send_opening_summary_if_needed)

        assert '"09:00"' not in src and "'09:00'" not in src, (
            "[TP-CODE-004d] CR-CODE-06: _send_opening_summary_if_needed still contains "
            "a hard-coded '09:00' string comparison. "
            "Replace with DB-idempotency check that allows any trading-session minute."
        )
