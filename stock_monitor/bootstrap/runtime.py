"""Runtime prerequisite checks and configuration validation."""

from __future__ import annotations

import re
from typing import Mapping


def assert_sqlite_prerequisites(conn) -> dict:
    """Fail-fast runtime checks for SQLite prerequisites."""
    cursor = conn.execute("PRAGMA foreign_keys;")
    foreign_keys = bool(cursor.fetchone()[0] == 1)
    if not foreign_keys:
        raise RuntimeError("foreign_keys must be ON")

    try:
        cursor = conn.execute("SELECT json_valid('[]');")
        json_ok = bool(cursor.fetchone()[0] == 1)
    except Exception as exc:  # sqlite3.OperationalError in normal path
        raise RuntimeError("JSON1 unavailable") from exc

    if not json_ok:
        raise RuntimeError("JSON1 unavailable")

    return {"foreign_keys": True, "json1": True}


def _pick_first_non_empty(env: Mapping[str, str], keys: list[str]) -> str | None:
    for key in keys:
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_valid_channel_token(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._\-]{10,}", token))


def _is_valid_group_id(group_id: str) -> bool:
    return bool(re.fullmatch(r"[CR][A-Za-z0-9]{10,}", group_id))


def validate_line_runtime_config(env: Mapping[str, str]) -> dict:
    """Validate LINE runtime config with canonical+alias support."""
    channel_token = _pick_first_non_empty(env, ["LINE_CHANNEL_ACCESS_TOKEN", "CHANNEL_ACCESS_TOKEN"])
    if channel_token is None:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN missing (alias: CHANNEL_ACCESS_TOKEN)")

    group_id = _pick_first_non_empty(env, ["LINE_TO_GROUP_ID", "TARGET_GROUP_ID"])
    if group_id is None:
        raise RuntimeError("LINE_TO_GROUP_ID missing (alias: TARGET_GROUP_ID)")

    if not _is_valid_channel_token(channel_token):
        raise RuntimeError("invalid channel token")

    if not _is_valid_group_id(group_id):
        raise RuntimeError("invalid group id")

    return {"channel_token": channel_token, "group_id": group_id}

