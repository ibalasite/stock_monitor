"""Idempotency helpers."""

from __future__ import annotations


def build_minute_idempotency_key(stock_no: str, minute_bucket: str, stock_status: int | None = None) -> str:
    """Minute-level idempotency key (intentionally ignores stock_status)."""
    _ = stock_status
    return f"{stock_no}|{minute_bucket}"

