"""Comparison engine: walk linked event pairs, emit ComparisonResults."""

from infrareplay.comparison.engine import compare_run
from infrareplay.comparison.normalize import normalize

__all__ = ["compare_run", "normalize"]
