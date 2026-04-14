"""Template rendering primitives for LINE messages (FR-14 / FR-17).

FR-17 (EDD §2.8): File-based Jinja2 template loading.
  - Key whitelist: ^[a-z0-9_]+$ (OWASP A01 path traversal prevention)
  - Jinja2 Environment(loader=FileSystemLoader(template_dir), undefined=StrictUndefined, autoescape=False)
  - LINE_TEMPLATE_DIR env var overrides default templates/line/ directory
  - TemplateNotFound → fallback to built-in Python rendering + WARN log (TEMPLATE_NOT_FOUND)
  - Render error (StrictUndefined, etc.) → ERROR log (TEMPLATE_RENDER_FAILED) + fallback
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    UndefinedError,
)

logger = logging.getLogger(__name__)

# Key whitelist: only lowercase letters, digits, underscores (EDD §2.8 / OWASP A01)
_KEY_RE = re.compile(r'^[a-z0-9_]+$')

# Default template directory (relative to this file's package root)
_DEFAULT_TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates" / "line"


def _get_template_dir() -> Path:
    """Return the template directory, honouring LINE_TEMPLATE_DIR env var."""
    env_dir = os.environ.get("LINE_TEMPLATE_DIR")
    if env_dir:
        return Path(env_dir)
    return _DEFAULT_TEMPLATE_DIR


def _validate_key(template_key: str) -> None:
    """Raise ValueError if template_key fails the ^[a-z0-9_]+$ whitelist."""
    if not _KEY_RE.match(template_key):
        raise ValueError(
            f"Invalid template key {template_key!r}: "
            "must match ^[a-z0-9_]+$ (OWASP A01 – path traversal prevention)"
        )


class LineTemplateRenderer:
    """Renderer that loads .j2 files via Jinja2 FileSystemLoader (FR-17).

    Falls back to built-in Python rendering when the .j2 file is not found,
    emitting a WARN log.  Render errors (e.g. StrictUndefined) are logged as
    ERROR and the built-in fallback is also attempted.
    """

    def render(self, template_key: str, context: dict) -> str:
        _validate_key(template_key)

        template_dir = _get_template_dir()
        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            undefined=StrictUndefined,
            autoescape=False,
        )

        try:
            tmpl = env.get_template(f"{template_key}.j2")
            return tmpl.render(**context)
        except TemplateNotFound:
            logger.warning(
                "TEMPLATE_NOT_FOUND: template file '%s.j2' not found in '%s'; "
                "falling back to built-in rendering.",
                template_key,
                template_dir,
            )
            return self._builtin_render(template_key, context)
        except UndefinedError as exc:
            logger.error(
                "TEMPLATE_RENDER_FAILED: undefined variable while rendering "
                "'%s.j2': %s",
                template_key,
                exc,
            )
            return self._builtin_render(template_key, context)
        except Exception as exc:
            logger.error(
                "TEMPLATE_RENDER_FAILED: unexpected error while rendering "
                "'%s.j2': %s",
                template_key,
                exc,
            )
            return self._builtin_render(template_key, context)

    # ------------------------------------------------------------------
    # Built-in fallback rendering (FR-14 legacy behaviour)
    # ------------------------------------------------------------------

    def _builtin_render(self, template_key: str, context: dict) -> str:
        if "trigger_row_digest" in template_key:
            return self._render_trigger_row_digest(context)
        if "trigger_row" in template_key:
            return self._render_trigger_row(context)
        if "minute_digest" in template_key:
            minute_bucket = context.get("minute_bucket", "")
            return f"[股票監控通知] {minute_bucket}"
        if "test_push" in template_key:
            return f"[測試推播] {context.get('message', '')}"
        # opening_summary and generic fallback
        return "{stock_display} {method_label} {fair_price}/{cheap_price}".format(**context)

    def _render_trigger_row_digest(self, context: dict) -> str:
        idx = context.get("idx", "")
        base_message = context.get("base_message", "")
        methods = context.get("methods", "")
        if methods:
            return f"{idx}) {base_message}（命中方法: {methods}）"
        return f"{idx}) {base_message}"

    def _render_trigger_row(self, context: dict) -> str:
        label = context.get("display_label", context.get("stock_no", ""))
        price = context.get("current_price", "")
        status = int(context.get("stock_status", 1))
        cheap = context.get("cheap_price")
        fair = context.get("fair_price")
        if status == 2 and cheap is not None:
            text = f"{label}目前{price}，低於便宜價{cheap}"
            if fair is not None and fair != cheap:
                text += f"（合理價{fair}）"
            return text
        if fair is not None:
            return f"{label}目前{price}，低於合理價{fair}"
        return f"{label}目前{price}，觸發監控門檻"


def render_line_template_message(template_key: str, context: dict) -> str:
    """Single canonical entry point for all LINE template rendering (CR-ARCH-03)."""
    return LineTemplateRenderer().render(template_key, context)

