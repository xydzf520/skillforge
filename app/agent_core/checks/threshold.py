"""Threshold check helper."""

from __future__ import annotations


def evaluate_threshold(value: float, warning: float, critical: float) -> str:
    if value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "ok"
