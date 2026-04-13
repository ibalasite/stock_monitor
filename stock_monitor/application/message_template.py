"""Template rendering primitives for LINE messages (FR-14)."""

from __future__ import annotations


class LineTemplateRenderer:
    """Minimal renderer contract used by runtime message composition."""

    def render(self, template_key: str, context: dict) -> str:
        if "trigger_row" in template_key:
            return self._render_trigger_row(context)
        if "minute_digest" in template_key:
            minute_bucket = context.get("minute_bucket", "")
            return f"[股票監控通知] {minute_bucket}"
        if "test_push" in template_key:
            return f"[測試推播] {context.get('message', '')}"
        # opening_summary and fallback
        return "{stock_display} {method_label} {fair_price}/{cheap_price}".format(**context)

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
    return LineTemplateRenderer().render(template_key, context)

