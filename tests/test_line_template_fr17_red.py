"""Red-light TDD tests for FR-17 File-based Jinja2 Template Loading.

EDD §2.8 要求 LineTemplateRenderer 使用：
  - Jinja2 Environment(loader=FileSystemLoader(template_dir), undefined=StrictUndefined, autoescape=False)
  - Key 白名單 ^[a-z0-9_]+$，拒絕含 /../\\ 的 key（OWASP A01 path traversal）
  - LINE_TEMPLATE_DIR env var 覆蓋預設目錄
  - TemplateNotFound → fallback 至內建預設 + WARN log (TEMPLATE_NOT_FOUND)
  - 渲染失敗 → ERROR log (TEMPLATE_RENDER_FAILED)，不得送出硬編碼未知格式

目前 LineTemplateRenderer 使用 hardcoded if/else Python 邏輯，以上全部未實作。
這些測試應全部亮 RED。
"""

from __future__ import annotations

import logging
import os

import pytest

from stock_monitor.application.message_template import (
    LineTemplateRenderer,
    render_line_template_message,
)


# ---------------------------------------------------------------------------
# TP-FR17-001 ~ 004  Key 白名單驗證
# ---------------------------------------------------------------------------

class TestKeyWhitelistValidation:
    """EDD §2.8: key 白名單 ^[a-z0-9_]+$；拒絕 /、\\、.. 等路徑字元。"""

    @pytest.mark.parametrize("bad_key", [
        "../secret",
        "../../etc/passwd",
        "foo/bar",
        "foo\\bar",
        "foo..bar",
        "/absolute",
        "FOO",       # uppercase not in ^[a-z0-9_]+$
        "foo bar",   # space
        "foo-bar",   # hyphen
    ])
    def test_tp_fr17_001_invalid_key_raises_value_error(self, bad_key: str):
        """[TP-FR17-001/002/003] 不符合 ^[a-z0-9_]+$ 的 key 必須 raise ValueError。

        Currently LineTemplateRenderer silently proceeds — this test is RED.
        """
        renderer = LineTemplateRenderer()
        with pytest.raises(ValueError, match=r"(?i)(invalid|illegal|key|not allowed|whitelist|template)"):
            renderer.render(bad_key, {})

    @pytest.mark.parametrize("good_key", [
        "line_minute_digest_v1",
        "line_trigger_row_v1",
        "line_test_push_v1",
        "line_opening_summary_mobile_compact_v1",
    ])
    def test_tp_fr17_004_valid_key_passes_whitelist(self, good_key: str):
        """[TP-FR17-004] 合法 key（^[a-z0-9_]+$）不應因白名單驗證而 raise。

        This test is GREEN only when whitelist logic exists AND still allows valid keys.
        Currently this is RED because no whitelist exists — bad keys don't raise,
        and the test expectation that an exception is NOT raised may coincidentally pass;
        but the explicit whitelist check (that a valid key passes) is only meaningful
        once the invalid-key tests also pass (i.e., whitelist is implemented).
        We mark this test as a companion to TP-FR17-001: both signal the same gap.
        We assert that the render at minimum returns a non-empty string.
        """
        renderer = LineTemplateRenderer()
        # Should not raise — valid key
        try:
            result = renderer.render(good_key, {
                "minute_bucket": "2026-04-15 10:00",
                "message": "test",
                "stock_display": "台積電(2330)",
                "method_label": "手動",
                "fair_price": "2000",
                "cheap_price": "1500",
            })
            assert result is not None
        except ValueError as exc:
            pytest.fail(
                f"[TP-FR17-004] Valid key '{good_key}' must NOT raise ValueError. Got: {exc}"
            )


# ---------------------------------------------------------------------------
# TP-FR17-005 ~ 006  LINE_TEMPLATE_DIR env var + Jinja2 file rendering
# ---------------------------------------------------------------------------

class TestLineTemplateDirEnvVar:
    """EDD §2.8: LINE_TEMPLATE_DIR 指定模板目錄；.j2 檔內容透過 Jinja2 渲染。"""

    def test_tp_fr17_005_custom_template_dir_is_read(self, tmp_path, monkeypatch):
        """[TP-FR17-005] LINE_TEMPLATE_DIR 指向含 .j2 檔的目錄時讀取並渲染該檔。

        Currently render_line_template_message ignores LINE_TEMPLATE_DIR entirely.
        This test is RED.
        """
        (tmp_path / "line_minute_digest_v1.j2").write_text(
            "CUSTOM:{{ minute_bucket }}", encoding="utf-8"
        )
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))

        result = render_line_template_message(
            "line_minute_digest_v1",
            {"minute_bucket": "2026-04-15 10:00"},
        )

        assert "CUSTOM:" in result, (
            "[TP-FR17-005] render_line_template_message must read .j2 file from LINE_TEMPLATE_DIR. "
            f"Got: {result!r}\n"
            "Currently the renderer ignores LINE_TEMPLATE_DIR and returns hardcoded Python output."
        )
        assert "2026-04-15 10:00" in result

    def test_tp_fr17_006_jinja2_renders_file_content_not_hardcoded(self, tmp_path, monkeypatch):
        """[TP-FR17-006] Jinja2 渲染 .j2 檔案內容，不使用 hardcoded if/else Python 邏輯。

        The .j2 file is the source of truth. If the implementation uses hardcoded
        Python logic, this test will fail because the output won't match the file.
        Currently RED.
        """
        (tmp_path / "line_test_push_v1.j2").write_text(
            "PUSH:{{ message }}:END", encoding="utf-8"
        )
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))

        result = render_line_template_message(
            "line_test_push_v1",
            {"message": "hello"},
        )

        assert result == "PUSH:hello:END", (
            f"[TP-FR17-006] Expected 'PUSH:hello:END' (from .j2 file). Got: {result!r}\n"
            "Currently the renderer returns '[測試推播] hello' from hardcoded Python."
        )


# ---------------------------------------------------------------------------
# TP-FR17-007  TemplateNotFound → fallback + WARN log
# ---------------------------------------------------------------------------

class TestTemplateFallback:
    """EDD §2.8: TemplateNotFound → fallback 至內建預設 + WARN log (TEMPLATE_NOT_FOUND)。"""

    def test_tp_fr17_007_missing_template_fallback_no_raise(self, tmp_path, monkeypatch):
        """[TP-FR17-007] 模板檔案不存在時 fallback 至內建預設，不 raise，不靜默。

        Currently the renderer never reads files so TemplateNotFound is never triggered.
        This test is RED: it checks that a WARN log event 'TEMPLATE_NOT_FOUND' is emitted,
        which requires file-based loading to exist first.
        """
        # Empty directory — no .j2 files
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))

        warn_records: list[str] = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.WARNING:
                    warn_records.append(record.getMessage())

        logger = logging.getLogger("stock_monitor.application.message_template")
        handler = _CapturingHandler()
        logger.addHandler(handler)
        try:
            # Should NOT raise — must fallback
            result = render_line_template_message(
                "line_minute_digest_v1",
                {"minute_bucket": "2026-04-15 10:00"},
            )
        finally:
            logger.removeHandler(handler)

        # Must produce some output (fallback)
        assert result, "[TP-FR17-007] result must be non-empty even when .j2 file is missing"

        # Must emit WARN with TEMPLATE_NOT_FOUND
        assert any("TEMPLATE_NOT_FOUND" in msg for msg in warn_records), (
            "[TP-FR17-007] A WARN log with 'TEMPLATE_NOT_FOUND' must be emitted when "
            "the .j2 template file is not found.\n"
            f"  Captured log messages: {warn_records!r}\n"
            "  Currently the renderer never reads files, so this WARN never fires."
        )


# ---------------------------------------------------------------------------
# TP-FR17-008  StrictUndefined — 未定義變數報錯
# ---------------------------------------------------------------------------

class TestStrictUndefined:
    """EDD §2.8: StrictUndefined — 未定義變數立即報錯；禁止靜默空字串。"""

    def test_tp_fr17_008_undefined_variable_logs_render_failed(self, tmp_path, monkeypatch):
        """[TP-FR17-008] 模板中 {{ undefined_var }} 搭配空 context 時記錄 TEMPLATE_RENDER_FAILED。

        With Jinja2 StrictUndefined, referencing an undefined var will raise UndefinedError.
        The renderer must catch it, log TEMPLATE_RENDER_FAILED (ERROR), and either
        return a fallback string or re-raise — it must NOT silently return an empty string.
        Currently RED: no Jinja2 → no StrictUndefined → no error logging.
        """
        (tmp_path / "line_test_push_v1.j2").write_text(
            "{{ undefined_var }}", encoding="utf-8"
        )
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))

        error_records: list[str] = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR:
                    error_records.append(record.getMessage())

        logger = logging.getLogger("stock_monitor.application.message_template")
        handler = _CapturingHandler()
        logger.addHandler(handler)
        render_raised: Exception | None = None
        try:
            render_line_template_message("line_test_push_v1", {})
        except Exception as exc:
            render_raised = exc
        finally:
            logger.removeHandler(handler)

        # Either the renderer raised (StrictUndefined UndefinedError), OR it must
        # have logged TEMPLATE_RENDER_FAILED. Silently returning junk is forbidden.
        if render_raised is None:
            assert any("TEMPLATE_RENDER_FAILED" in msg for msg in error_records), (
                "[TP-FR17-008] When template has undefined variable, must log "
                f"'TEMPLATE_RENDER_FAILED'. Got logs: {error_records!r}\n"
                "Currently no Jinja2 StrictUndefined is used, so this never fires."
            )


# ---------------------------------------------------------------------------
# TP-FR17-009  LineTemplateRenderer 使用 Jinja2（架構驗證）
# ---------------------------------------------------------------------------

class TestJinja2Architecture:
    """EDD §2.8: LineTemplateRenderer 必須使用 Jinja2 Environment + FileSystemLoader。"""

    def test_tp_fr17_009_renderer_uses_jinja2_environment(self):
        """[TP-FR17-009] LineTemplateRenderer 原始碼中必須有 Jinja2 Environment 和 FileSystemLoader。

        Currently the implementation uses hardcoded if/else Python logic with no Jinja2.
        This test is RED until FR-17 is implemented.
        """
        import inspect
        source = inspect.getsource(LineTemplateRenderer)

        has_jinja2 = (
            "FileSystemLoader" in source
            or "Environment" in source
            or "jinja2" in source.lower()
        )
        assert has_jinja2, (
            "[TP-FR17-009] LineTemplateRenderer must use Jinja2 Environment with FileSystemLoader.\n"
            "  EDD §2.8: Environment(loader=FileSystemLoader(template_dir), "
            "undefined=StrictUndefined, autoescape=False)\n"
            "  Currently the renderer uses hardcoded if/else Python logic:\n"
            "    if 'trigger_row_digest' in template_key: ...\n"
            "    if 'trigger_row' in template_key: ...\n"
            "  → Replace with Jinja2 FileSystemLoader-based rendering."
        )

    def test_tp_fr17_009b_jinja2_package_must_be_importable(self):
        """[TP-FR17-009b] jinja2 package が必要な依存として installable でなければならない。

        FR-17 の実装に jinja2 が必要。現在インストールされていない場合は RED。
        """
        try:
            import jinja2  # noqa: F401
        except ImportError:
            pytest.fail(
                "[TP-FR17-009b] `jinja2` must be installed as a dependency for FR-17.\n"
                "  Add `jinja2` to requirements and run `pip install jinja2`.\n"
                "  EDD §2.8 requires: Jinja2 Environment(loader=FileSystemLoader(...))"
            )


# ---------------------------------------------------------------------------
# Coverage gate — builtin fallback branches (all via TemplateNotFound path)
# ---------------------------------------------------------------------------

class TestBuiltinFallbackCoverage:
    """Extra branches in _builtin_render / _render_trigger_row / _render_trigger_row_digest
    that are only reachable when the .j2 file is absent (TemplateNotFound → fallback)."""

    def test_builtin_fallback_trigger_row_digest_with_methods(self, tmp_path, monkeypatch):
        """_builtin_render('line_trigger_row_digest_v1') with methods non-empty."""
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))  # no .j2 files
        result = render_line_template_message(
            "line_trigger_row_digest_v1",
            {"idx": "1", "base_message": "台積電(2330)目前2050，低於合理價2000", "methods": "manual"},
        )
        assert "命中方法" in result
        assert "manual" in result

    def test_builtin_fallback_trigger_row_digest_no_methods(self, tmp_path, monkeypatch):
        """_builtin_render('line_trigger_row_digest_v1') with methods empty."""
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))
        result = render_line_template_message(
            "line_trigger_row_digest_v1",
            {"idx": "2", "base_message": "海悅(2348)觸發", "methods": ""},
        )
        assert "2)" in result
        assert "命中方法" not in result

    def test_builtin_fallback_opening_summary_generic(self, tmp_path, monkeypatch):
        """_builtin_render generic fallback branch (opening_summary key)."""
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))
        result = render_line_template_message(
            "line_opening_summary_row_compact_v1",
            {"stock_display": "台積電(2330)", "method_label": "手動",
             "fair_price": "2000", "cheap_price": "1500"},
        )
        assert "台積電(2330)" in result
        assert "2000" in result

    def test_builtin_fallback_trigger_row_status2_with_fair(self, tmp_path, monkeypatch):
        """_render_trigger_row status=2 branch when fair_price != cheap_price."""
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))
        result = render_line_template_message(
            "line_trigger_row_v1",
            {"display_label": "台積電(2330)", "current_price": "1400",
             "stock_status": 2, "cheap_price": 1500, "fair_price": 2000},
        )
        assert "便宜價" in result
        assert "合理價" in result

    def test_builtin_fallback_trigger_row_status2_fair_equals_cheap(self, tmp_path, monkeypatch):
        """_render_trigger_row status=2 branch when fair_price == cheap_price (no 合理價 suffix)."""
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))
        result = render_line_template_message(
            "line_trigger_row_v1",
            {"display_label": "台積電(2330)", "current_price": "1400",
             "stock_status": 2, "cheap_price": 1500, "fair_price": 1500},
        )
        assert "便宜價" in result
        assert "合理價" not in result

    def test_builtin_fallback_trigger_row_no_fair(self, tmp_path, monkeypatch):
        """_render_trigger_row fallback: no fair_price → 觸發監控門檻."""
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))
        result = render_line_template_message(
            "line_trigger_row_v1",
            {"display_label": "台積電(2330)", "current_price": "1400",
             "stock_status": 1, "cheap_price": None, "fair_price": None},
        )
        assert "觸發監控門檻" in result

    def test_generic_exception_path_logs_render_failed(self, tmp_path, monkeypatch):
        """except Exception handler (non-UndefinedError) logs TEMPLATE_RENDER_FAILED."""
        from unittest.mock import patch

        (tmp_path / "line_test_push_v1.j2").write_text("{{ message }}", encoding="utf-8")
        monkeypatch.setenv("LINE_TEMPLATE_DIR", str(tmp_path))

        error_records: list[str] = []

        class _Cap(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR:
                    error_records.append(record.getMessage())

        lg = logging.getLogger("stock_monitor.application.message_template")
        h = _Cap()
        lg.addHandler(h)
        try:
            # Patch Template.render (inside the try block) to raise a generic RuntimeError
            with patch("jinja2.Template.render", side_effect=RuntimeError("forced generic error")):
                result = render_line_template_message("line_test_push_v1", {"message": "x"})
        finally:
            lg.removeHandler(h)

        assert any("TEMPLATE_RENDER_FAILED" in m for m in error_records)
        assert result is not None  # fallback returned something
