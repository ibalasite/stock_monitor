"""Domain policies."""

from __future__ import annotations

from dataclasses import dataclass


class PriorityPolicy:
    """Priority rule: status 2 overrides status 1."""

    def resolve_status(self, statuses: list[int]) -> int | None:
        if not statuses:
            return None
        return 2 if 2 in statuses else 1


@dataclass
class CooldownPolicy:
    cooldown_seconds: int = 300

    def can_send(self, last_sent_at: int | None, now_ts: int) -> bool:
        if last_sent_at is None:
            return True
        return (now_ts - last_sent_at) >= self.cooldown_seconds


def aggregate_stock_signals(stock_no: str, hits: list[dict]) -> list[dict]:
    """Aggregate per-stock method hits to one event with full methods list."""
    if not hits:
        return []

    statuses = [int(hit["stock_status"]) for hit in hits]
    methods = sorted(
        {
            str(hit.get("method") or hit.get("method_name") or "")
            for hit in hits
            if str(hit.get("method") or hit.get("method_name") or "")
        }
    )

    policy = PriorityPolicy()
    effective_status = policy.resolve_status(statuses)

    return [
        {
            "stock_no": stock_no,
            "stock_status": effective_status,
            "methods_hit": methods,
        }
    ]

