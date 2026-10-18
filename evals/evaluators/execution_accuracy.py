"""Execution accuracy: does the generated query's result set equal the gold result set?

Column names are ignored (models alias differently); column count, values and row count must
match. Row order matters only when the gold query has a meaningful ORDER BY.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

REL_TOL = 1e-3
ABS_TOL = 5e-3


def _cell_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return str(a) == str(b)


def _row_equal(a: Sequence[Any], b: Sequence[Any]) -> bool:
    return len(a) == len(b) and all(_cell_equal(x, y) for x, y in zip(a, b, strict=True))


def results_match(
    gold_rows: Sequence[Sequence[Any]],
    got_rows: Sequence[Sequence[Any]],
    *,
    order_matters: bool,
) -> bool:
    if len(gold_rows) != len(got_rows):
        return False
    if order_matters:
        return all(_row_equal(g, r) for g, r in zip(gold_rows, got_rows, strict=True))
    remaining = list(got_rows)
    for g in gold_rows:
        for i, r in enumerate(remaining):
            if _row_equal(g, r):
                del remaining[i]
                break
        else:
            return False
    return True
