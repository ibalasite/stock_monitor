"""Application workflow helpers for intraday monitoring."""

from __future__ import annotations

import uuid


def _normalize_methods(methods) -> list[str]:
    if methods is None:
        return []
    if isinstance(methods, str):
        parts = [part.strip() for part in methods.split(",")]
        return sorted({part for part in parts if part})
    return sorted({str(item).strip() for item in methods if str(item).strip()})


def aggregate_minute_notifications(minute_bucket: str, signals: list[dict]) -> str:
    lines = [f"[Stock Minute Digest] {minute_bucket}"]
    for idx, signal in enumerate(signals, start=1):
        methods = ",".join(_normalize_methods(signal.get("methods_hit")))
        lines.append(
            f"{idx}) {signal.get('stock_no')} | status={signal.get('stock_status')} | methods={methods}"
        )
    return "\n".join(lines)


def merge_minute_message(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    existing_status = int(existing.get("stock_status", 1))
    incoming_status = int(incoming.get("stock_status", 1))

    merged["stock_no"] = incoming.get("stock_no", existing.get("stock_no"))
    merged["minute_bucket"] = incoming.get("minute_bucket", existing.get("minute_bucket"))
    merged["stock_status"] = max(existing_status, incoming_status)
    merged["methods_hit"] = _normalize_methods(
        _normalize_methods(existing.get("methods_hit")) + _normalize_methods(incoming.get("methods_hit"))
    )
    merged["message"] = incoming.get("message", existing.get("message"))
    return merged


def dispatch_and_persist_minute(
    minute_bucket: str,
    rows: list[dict],
    line_client,
    message_repo,
    pending_repo,
    pending_fallback,
    logger,
) -> dict:
    payload = aggregate_minute_notifications(minute_bucket, rows)

    try:
        line_client.send(payload)
    except Exception as exc:
        logger.log("ERROR", f"LINE_SEND_FAILED: {exc}")
        return {"status": "line_failed", "sent": False}

    try:
        message_repo.save_batch(rows)
        return {"status": "persisted", "sent": True}
    except Exception as exc:
        pending_item = {
            "pending_id": f"P-{uuid.uuid4().hex}",
            "status": "PENDING",
            "minute_bucket": minute_bucket,
            "payload": payload,
            "rows": rows,
            "error": str(exc),
        }
        try:
            pending_repo.enqueue(pending_item)
        except Exception as ledger_exc:
            pending_fallback.append(pending_item)
            logger.log("WARN", f"PENDING_FALLBACK_JSONL: {ledger_exc}")
        return {"status": "pending", "sent": True}


def reconcile_pending_once(line_client, message_repo, pending_repo, logger) -> dict:
    reconciled = 0
    for item in pending_repo.list_pending():
        try:
            # Pending items represent "LINE already sent, DB persist failed".
            # Reconcile must only backfill DB state and never re-send LINE.
            _ = line_client
            message_repo.save_batch(item.get("rows", []))
            pending_repo.mark_reconciled(item.get("pending_id"))
            reconciled += 1
        except Exception as exc:
            logger.log("ERROR", f"RECONCILE_FAILED: {exc}")
    return {"reconciled": reconciled}


def guard_minute_execution(now_epoch: int, market_data_provider, logger) -> dict:
    try:
        snapshot = market_data_provider.get_market_snapshot(now_epoch)
    except TimeoutError:
        logger.log("WARN", "MARKET_TIMEOUT")
        return {"should_run": False, "reason": "MARKET_TIMEOUT", "skip_minute": True, "backfill_allowed": False}
    except Exception as exc:
        logger.log("WARN", f"MARKET_ERROR: {exc}")
        return {"should_run": False, "reason": "MARKET_ERROR", "skip_minute": True, "backfill_allowed": False}

    return {"should_run": True, "snapshot": snapshot}


def persist_message_rows_transactional(repo, rows: list[dict]) -> None:
    repo.begin()
    try:
        for row in rows:
            repo.insert_row(row)
        repo.commit()
    except Exception:
        repo.rollback()
        raise


def fetch_market_with_retry(
    now_epoch: int,
    market_data_provider,
    max_retries: int,
    logger,
) -> dict:
    for attempt in range(max_retries + 1):
        try:
            snapshot = market_data_provider.get_market_snapshot(now_epoch)
            return {"ok": True, "snapshot": snapshot}
        except TimeoutError:
            if attempt < max_retries:
                logger.log("WARN", f"retry {attempt + 1}/{max_retries}")
                continue
            logger.log("WARN", "MARKET_TIMEOUT")
            return {"ok": False, "skip_minute": True, "backfill_allowed": False}
        except Exception as exc:
            logger.log("ERROR", f"MARKET_FETCH_FAILED: {exc}")
            return {"ok": False, "skip_minute": True, "backfill_allowed": False}

    return {"ok": False, "skip_minute": True, "backfill_allowed": False}
