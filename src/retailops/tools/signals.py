"""Heuristics that tell the SQL Analyst a result "looks corrupted" and should be handed to the
Data Quality specialist (docs/02 handoff pattern). These are cheap row-level signals, not proof:
a handoff only triggers extra read-only checks, it never changes data or the SQL answer."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

_MONEY_COLUMN = re.compile(r"(revenue|amount|payment|price|total)", re.IGNORECASE)
MIN_ROWS_FOR_DUPLICATES = 2


@dataclass(frozen=True)
class CorruptionSignal:
    kind: str  # name of the DQ check that should run
    reason: str


def corruption_signals(columns: list[str], rows: list[list[Any]]) -> list[CorruptionSignal]:
    signals: list[CorruptionSignal] = []
    money_idx = [i for i, c in enumerate(columns) if _MONEY_COLUMN.search(c)]
    bad = 0
    for row in rows:
        for i in money_idx:
            v = row[i] if i < len(row) else None
            if isinstance(v, int | float) and not isinstance(v, bool) and v <= 0:
                bad += 1
                break
    if bad:
        signals.append(
            CorruptionSignal(
                "invalid_payments",
                f"{bad} row(s) have a zero or negative value in a money column",
            )
        )
    if len(rows) >= MIN_ROWS_FOR_DUPLICATES:
        counts = Counter(tuple(map(repr, r)) for r in rows)
        dupes = sum(n - 1 for n in counts.values() if n > 1)
        if dupes:
            signals.append(
                CorruptionSignal(
                    "duplicate_events", f"{dupes} fully identical row(s) in the result"
                )
            )
    return signals
