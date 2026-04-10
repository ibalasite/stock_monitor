"""Metrics helpers."""

from __future__ import annotations


def compute_notification_accuracy(
    total_signal_minutes: int,
    outage_minutes: int,
    correct_notified_minutes: int,
) -> dict:
    effective_denominator = max(total_signal_minutes - outage_minutes, 0)
    if effective_denominator == 0:
        accuracy = 0.0
    else:
        accuracy = correct_notified_minutes / effective_denominator

    return {
        "effective_denominator": effective_denominator,
        "accuracy": accuracy,
        "pass": accuracy >= 0.99,
    }

