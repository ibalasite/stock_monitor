"""BDD step definitions for FR-17: File-based Jinja2 Template Loading.

These steps implement the scenarios defined in features/line_template_fr17.feature.
All scenarios should be RED until FR-17 is implemented.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, then, when


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def fr17_ctx() -> dict[str, Any]:
    """Shared context dict passed between Given/When/Then steps."""
    return {}


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------

@given("LineTemplateRenderer is available in stock_monitor.application.message_template")
def given_renderer_available(fr17_ctx: dict):
    from stock_monitor.application.message_template import (
        LineTemplateRenderer,
        render_line_template_message,
    )
    fr17_ctx["LineTemplateRenderer"] = LineTemplateRenderer
    fr17_ctx["render_line_template_message"] = render_line_template_message


# ---------------------------------------------------------------------------
# Given steps — key
# ---------------------------------------------------------------------------

@given('template key "../secret"')
def given_key_path_traversal(fr17_ctx: dict):
    fr17_ctx["template_key"] = "../secret"


@given('template key "foo/bar"')
def given_key_slash(fr17_ctx: dict):
    fr17_ctx["template_key"] = "foo/bar"


@given('template key "foo\\\\bar"')
def given_key_backslash(fr17_ctx: dict):
    fr17_ctx["template_key"] = "foo\\bar"


@given('template key "line_minute_digest_v1"')
def given_key_valid(fr17_ctx: dict):
    fr17_ctx["template_key"] = "line_minute_digest_v1"


# ---------------------------------------------------------------------------
# Given steps — LINE_TEMPLATE_DIR & files
# ---------------------------------------------------------------------------

@given("LINE_TEMPLATE_DIR is set to a temp directory")
def given_template_dir_temp(fr17_ctx: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))
    fr17_ctx["template_dir"] = tmp_path


@given("LINE_TEMPLATE_DIR is set to an empty temp directory")
def given_template_dir_empty(fr17_ctx: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))
    fr17_ctx["template_dir"] = tmp_path


@given('a template file "line_minute_digest_v1.j2" in that directory containing "CUSTOM:{{ minute_bucket }}"')
def given_minute_digest_j2(fr17_ctx: dict):
    template_dir: Path = fr17_ctx["template_dir"]
    (template_dir / "line_minute_digest_v1.j2").write_text(
        "CUSTOM:{{ minute_bucket }}", encoding="utf-8"
    )


@given('a template file "line_test_push_v1.j2" in that directory containing "PUSH:{{ message }}"')
def given_test_push_j2_push(fr17_ctx: dict):
    template_dir: Path = fr17_ctx["template_dir"]
    (template_dir / "line_test_push_v1.j2").write_text(
        "PUSH:{{ message }}", encoding="utf-8"
    )


@given('a template file "line_test_push_v1.j2" in that directory containing "{{ undefined_var }}"')
def given_test_push_j2_undefined(fr17_ctx: dict):
    template_dir: Path = fr17_ctx["template_dir"]
    (template_dir / "line_test_push_v1.j2").write_text(
        "{{ undefined_var }}", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------

@when("render_line_template_message is called with that key and empty context")
def when_render_bad_key_empty_ctx(fr17_ctx: dict):
    render = fr17_ctx["render_line_template_message"]
    key = fr17_ctx["template_key"]
    fr17_ctx["exception"] = None
    fr17_ctx["result"] = None
    try:
        fr17_ctx["result"] = render(key, {})
    except Exception as exc:
        fr17_ctx["exception"] = exc


@when('render_line_template_message is called with that key and context {"minute_bucket": "2026-04-15 10:00"}')
def when_render_valid_key_minute_ctx(fr17_ctx: dict):
    render = fr17_ctx["render_line_template_message"]
    key = fr17_ctx["template_key"]
    fr17_ctx["exception"] = None
    fr17_ctx["result"] = None
    try:
        fr17_ctx["result"] = render(key, {"minute_bucket": "2026-04-15 10:00"})
    except Exception as exc:
        fr17_ctx["exception"] = exc


@when('render_line_template_message is called with key "line_minute_digest_v1" and context {"minute_bucket": "2026-04-15 10:00"}')
def when_render_minute_digest_key(fr17_ctx: dict):
    render = fr17_ctx["render_line_template_message"]
    fr17_ctx["exception"] = None
    fr17_ctx["result"] = None
    # Capture WARN logs
    warn_records: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warn_records.append(record.getMessage())

    logger = logging.getLogger("stock_monitor.application.message_template")
    handler = _Cap()
    logger.addHandler(handler)
    try:
        fr17_ctx["result"] = render("line_minute_digest_v1", {"minute_bucket": "2026-04-15 10:00"})
    except Exception as exc:
        fr17_ctx["exception"] = exc
    finally:
        logger.removeHandler(handler)
    fr17_ctx["warn_records"] = warn_records


@when('render_line_template_message is called with key "line_test_push_v1" and context {"message": "hello"}')
def when_render_test_push_hello(fr17_ctx: dict):
    render = fr17_ctx["render_line_template_message"]
    fr17_ctx["exception"] = None
    fr17_ctx["result"] = None
    try:
        fr17_ctx["result"] = render("line_test_push_v1", {"message": "hello"})
    except Exception as exc:
        fr17_ctx["exception"] = exc


@when("render_line_template_message is called with key \"line_test_push_v1\" and empty context")
def when_render_test_push_empty_ctx(fr17_ctx: dict):
    render = fr17_ctx["render_line_template_message"]
    fr17_ctx["exception"] = None
    fr17_ctx["result"] = None
    error_records: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                error_records.append(record.getMessage())

    logger = logging.getLogger("stock_monitor.application.message_template")
    handler = _Cap()
    logger.addHandler(handler)
    try:
        fr17_ctx["result"] = render("line_test_push_v1", {})
    except Exception as exc:
        fr17_ctx["exception"] = exc
    finally:
        logger.removeHandler(handler)
    fr17_ctx["error_records"] = error_records


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------

@then("a ValueError should be raised")
def then_value_error_raised(fr17_ctx: dict):
    exc = fr17_ctx.get("exception")
    assert isinstance(exc, ValueError), (
        f"[FR-17] Expected ValueError for bad template key. "
        f"Got exception={exc!r}, result={fr17_ctx.get('result')!r}\n"
        "Currently LineTemplateRenderer has no key whitelist validation → RED."
    )


@then("no exception should be raised")
def then_no_exception(fr17_ctx: dict):
    exc = fr17_ctx.get("exception")
    assert exc is None, f"[FR-17] Expected no exception but got: {exc!r}"


@then("the result should be non-empty")
def then_result_non_empty(fr17_ctx: dict):
    result = fr17_ctx.get("result")
    assert result, f"[FR-17] Expected non-empty result. Got: {result!r}"


@then('the rendered output should contain "CUSTOM:"')
def then_output_contains_custom(fr17_ctx: dict):
    result = fr17_ctx.get("result")
    assert result and "CUSTOM:" in result, (
        f"[TP-FR17-005] Expected 'CUSTOM:' in output. Got: {result!r}\n"
        "Currently LINE_TEMPLATE_DIR is ignored → result is hardcoded Python output → RED."
    )


@then('the rendered output should contain "2026-04-15 10:00"')
def then_output_contains_date(fr17_ctx: dict):
    result = fr17_ctx.get("result")
    assert result and "2026-04-15 10:00" in result, (
        f"[TP-FR17-005] Expected '2026-04-15 10:00' in output. Got: {result!r}"
    )


@then('the rendered output should be "PUSH:hello"')
def then_output_equals_push(fr17_ctx: dict):
    result = fr17_ctx.get("result")
    assert result == "PUSH:hello", (
        f"[TP-FR17-006] Expected 'PUSH:hello' (from .j2 file). Got: {result!r}\n"
        "Currently returns '[測試推播] hello' from hardcoded Python → RED."
    )


@then('a WARN log with event "TEMPLATE_NOT_FOUND" should be emitted')
def then_warn_template_not_found(fr17_ctx: dict):
    warn_records = fr17_ctx.get("warn_records", [])
    assert any("TEMPLATE_NOT_FOUND" in msg for msg in warn_records), (
        f"[TP-FR17-007] Expected WARN log with 'TEMPLATE_NOT_FOUND'.\n"
        f"  Captured logs: {warn_records!r}\n"
        "  Currently no file-based loading exists → WARN never fires → RED."
    )


@then('a ERROR log with event "TEMPLATE_RENDER_FAILED" should be emitted or an exception should be raised')
def then_error_render_failed(fr17_ctx: dict):
    error_records = fr17_ctx.get("error_records", [])
    exc = fr17_ctx.get("exception")
    has_error_log = any("TEMPLATE_RENDER_FAILED" in msg for msg in error_records)
    assert has_error_log or exc is not None, (
        f"[TP-FR17-008] Expected 'TEMPLATE_RENDER_FAILED' ERROR log or exception for undefined variable.\n"
        f"  Captured error logs: {error_records!r}, exception={exc!r}\n"
        "  Currently no Jinja2 StrictUndefined → no error → RED."
    )


# ---------------------------------------------------------------------------
# TP-FR17-009  Architecture inspection
# ---------------------------------------------------------------------------

@when("the source code of LineTemplateRenderer is inspected")
def when_inspect_source(fr17_ctx: dict):
    import inspect
    renderer_cls = fr17_ctx["LineTemplateRenderer"]
    fr17_ctx["source"] = inspect.getsource(renderer_cls)


@then('it should reference "FileSystemLoader" or "Environment" or "jinja2"')
def then_source_references_jinja2(fr17_ctx: dict):
    source = fr17_ctx.get("source", "")
    has_jinja2 = (
        "FileSystemLoader" in source
        or "Environment" in source
        or "jinja2" in source.lower()
    )
    assert has_jinja2, (
        "[TP-FR17-009] LineTemplateRenderer source must reference Jinja2 "
        "FileSystemLoader / Environment.\n"
        "  EDD §2.8: Environment(loader=FileSystemLoader(template_dir), "
        "undefined=StrictUndefined, autoescape=False)\n"
        "  Currently uses hardcoded if/else Python logic → RED."
    )
