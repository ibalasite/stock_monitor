"""Health check helpers."""

from __future__ import annotations

from stock_monitor.bootstrap.runtime import assert_sqlite_prerequisites


def health_check(conn) -> dict:
    try:
        checks = assert_sqlite_prerequisites(conn)
    except RuntimeError as exc:
        return {"status": "error", "checks": {"foreign_keys": False, "json1": False}, "error": str(exc)}

    return {"status": "ok", "checks": checks}

