from __future__ import annotations

import sqlite3

from ._contract import require_symbol


class _CursorOne:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (self._value,)


class _NoJson1Connection:
    def execute(self, sql: str):
        lower = sql.lower()
        if "json_valid" in lower:
            raise sqlite3.OperationalError("no such function: json_valid")
        if "pragma foreign_keys" in lower:
            return _CursorOne(1)
        return _CursorOne(1)


def test_tp_env_001_fail_fast_when_json1_unavailable():
    assert_sqlite_prerequisites = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "assert_sqlite_prerequisites",
        "TP-ENV-001",
    )

    try:
        assert_sqlite_prerequisites(_NoJson1Connection())
        assert False, "[TP-ENV-001] Service must fail-fast when JSON1 is unavailable."
    except RuntimeError as exc:
        assert "json1" in str(exc).lower(), (
            "[TP-ENV-001] RuntimeError message should explicitly mention JSON1."
        )


def test_tp_env_002_foreign_keys_on_and_health_check_pass():
    assert_sqlite_prerequisites = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "assert_sqlite_prerequisites",
        "TP-ENV-002",
    )
    health_check = require_symbol(
        "stock_monitor.bootstrap.health",
        "health_check",
        "TP-ENV-002",
    )

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = ON;")

        assert_sqlite_prerequisites(conn)
        health = health_check(conn)
        assert isinstance(health, dict), "[TP-ENV-002] health_check must return dict."
        assert health.get("status") == "ok", (
            "[TP-ENV-002] health_check status must be 'ok' when prerequisites pass."
        )
        checks = health.get("checks", {})
        assert checks.get("foreign_keys") is True, (
            "[TP-ENV-002] health_check must report foreign_keys=True."
        )
    finally:
        conn.close()


def test_tp_env_003_line_runtime_config_validation_fail_fast():
    validate_line_runtime_config = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "validate_line_runtime_config",
        "TP-ENV-003",
    )

    try:
        validate_line_runtime_config({})
        assert False, "[TP-ENV-003] Missing LINE runtime config must fail-fast."
    except RuntimeError as exc:
        msg = str(exc)
        assert (
            "LINE_CHANNEL_ACCESS_TOKEN" in msg
            or "CHANNEL_ACCESS_TOKEN" in msg
        ), "[TP-ENV-003] Error must point to missing channel token."


def test_tp_env_003a_canonical_group_id_missing_fail_fast():
    validate_line_runtime_config = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "validate_line_runtime_config",
        "TP-ENV-003a",
    )
    try:
        validate_line_runtime_config({"LINE_CHANNEL_ACCESS_TOKEN": "valid-token-abc"})
        assert False, "[TP-ENV-003a] Missing LINE_TO_GROUP_ID must fail-fast."
    except RuntimeError as exc:
        msg = str(exc)
        assert (
            "LINE_TO_GROUP_ID" in msg or "TARGET_GROUP_ID" in msg
        ), "[TP-ENV-003a] Error must point to missing group id."


def test_tp_env_003b_alias_channel_token_missing_fail_fast():
    validate_line_runtime_config = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "validate_line_runtime_config",
        "TP-ENV-003b",
    )
    # Using alias naming: provide TARGET_GROUP_ID but omit CHANNEL_ACCESS_TOKEN
    try:
        validate_line_runtime_config({"TARGET_GROUP_ID": "C1234567890"})
        assert False, "[TP-ENV-003b] Missing CHANNEL_ACCESS_TOKEN alias must fail-fast."
    except RuntimeError as exc:
        msg = str(exc)
        assert (
            "CHANNEL_ACCESS_TOKEN" in msg or "LINE_CHANNEL_ACCESS_TOKEN" in msg
        ), "[TP-ENV-003b] Error must point to missing channel token (alias naming)."


def test_tp_env_003b_alias_group_id_missing_fail_fast():
    validate_line_runtime_config = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "validate_line_runtime_config",
        "TP-ENV-003b",
    )
    # Using alias naming: provide CHANNEL_ACCESS_TOKEN but omit TARGET_GROUP_ID
    try:
        validate_line_runtime_config({"CHANNEL_ACCESS_TOKEN": "valid-token-abc"})
        assert False, "[TP-ENV-003b] Missing TARGET_GROUP_ID alias must fail-fast."
    except RuntimeError as exc:
        msg = str(exc)
        assert (
            "TARGET_GROUP_ID" in msg or "LINE_TO_GROUP_ID" in msg
        ), "[TP-ENV-003b] Error must point to missing group id (alias naming)."


def test_tp_env_003c_invalid_channel_token_fail_fast():
    validate_line_runtime_config = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "validate_line_runtime_config",
        "TP-ENV-003c",
    )
    try:
        validate_line_runtime_config({
            "LINE_CHANNEL_ACCESS_TOKEN": "x",   # too short / obviously invalid format
            "LINE_TO_GROUP_ID": "C1234567890",
        })
        assert False, "[TP-ENV-003c] Invalid channel token format must fail-fast."
    except RuntimeError as exc:
        assert "invalid channel token" in str(exc).lower() or "token" in str(exc).lower(), (
            "[TP-ENV-003c] Error must indicate invalid channel token."
        )


def test_tp_env_003c_invalid_group_id_fail_fast():
    validate_line_runtime_config = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "validate_line_runtime_config",
        "TP-ENV-003c",
    )
    try:
        validate_line_runtime_config({
            "LINE_CHANNEL_ACCESS_TOKEN": "valid-looking-token-abc123xyz",
            "LINE_TO_GROUP_ID": "not-a-valid-group",   # wrong format
        })
        assert False, "[TP-ENV-003c] Invalid group id format must fail-fast."
    except RuntimeError as exc:
        assert "invalid group" in str(exc).lower() or "group" in str(exc).lower(), (
            "[TP-ENV-003c] Error must indicate invalid group id."
        )


def test_tp_env_003_error_message_must_not_expose_plaintext_token():
    validate_line_runtime_config = require_symbol(
        "stock_monitor.bootstrap.runtime",
        "validate_line_runtime_config",
        "TP-ENV-003",
    )
    secret_token = "super-secret-abc123xyz-do-not-log"
    try:
        validate_line_runtime_config({
            "LINE_CHANNEL_ACCESS_TOKEN": secret_token,
            "LINE_TO_GROUP_ID": "not-a-valid-group",
        })
    except RuntimeError as exc:
        assert secret_token not in str(exc), (
            "[TP-ENV-003] RuntimeError message must not expose the plaintext token value."
        )
