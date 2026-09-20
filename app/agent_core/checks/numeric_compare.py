"""Numeric comparison helper."""

from __future__ import annotations


def compare(value: float, threshold: float, operator: str = ">=") -> bool:
    if operator == ">":
        return value > threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    if operator == "==":
        return value == threshold
    return value >= threshold
