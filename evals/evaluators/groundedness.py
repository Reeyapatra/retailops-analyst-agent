"""Rule-based groundedness checks (docs/04-EVALUATION-PLAN.md section 3).

`numeric_groundedness`: every number in the answer must appear in the tool output (result rows),
or in the question / SQL text (dates, top-N limits), allowing rounding, percent/fraction
conversion and "million/thousand" phrasing. This is a necessary, not sufficient, check: it cannot
tell whether a correct number is attributed to the right entity. Known limits are listed in the
write-up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_NUMBER = re.compile(
    r"(?<![\w.])-?\$?\d[\d,]*(?:\.\d+)?(?:\s*(%|percent|million|billion|thousand|k\b|m\b))?",
    re.IGNORECASE,
)
_SCALE = {"million": 1e6, "m": 1e6, "billion": 1e9, "thousand": 1e3, "k": 1e3}


@dataclass(frozen=True)
class Number:
    value: float
    is_percent: bool
    text: str


def extract_numbers(text: str) -> list[Number]:
    out: list[Number] = []
    for m in _NUMBER.finditer(text):
        raw = m.group(0)
        suffix = (m.group(1) or "").lower()
        digits = re.sub(r"[^\d.\-]", "", raw[: len(raw) - len(m.group(1) or "")])
        if not digits or digits in {"-", "."}:
            continue
        try:
            value = float(digits)
        except ValueError:
            continue
        if suffix in _SCALE:
            value *= _SCALE[suffix]
        out.append(Number(value=value, is_percent=suffix in {"%", "percent"}, text=raw.strip()))
    return out


def _flatten(rows: list[list[Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        for cell in row:
            if isinstance(cell, bool):
                continue
            if isinstance(cell, int | float):
                values.append(float(cell))
            elif isinstance(cell, str):
                values.extend(n.value for n in extract_numbers(cell))
    return values


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.011, 0.005 * abs(b))


def _is_grounded(n: Number, allowed: list[float]) -> bool:
    candidates = [n.value] + ([n.value / 100.0] if n.is_percent else [])
    return any(_close(c, v) for c in candidates for v in allowed)


def numeric_groundedness(
    answer: str, *, rows: list[list[Any]], question: str = "", sql: str = ""
) -> dict[str, Any]:
    numbers = extract_numbers(answer)
    allowed = _flatten(rows)
    allowed += [n.value for n in extract_numbers(question)]
    allowed += [n.value for n in extract_numbers(sql)]
    ungrounded = [n.value for n in numbers if not _is_grounded(n, allowed)]
    score = 1.0 if not numbers else (len(numbers) - len(ungrounded)) / len(numbers)
    return {
        "key": "numeric_groundedness",
        "score": score,
        "n_numbers": len(numbers),
        "ungrounded": ungrounded,
    }


def citation_validity(*, cited: list[str], retrieved: list[str]) -> dict[str, Any]:
    """Fraction of cited chunk ids that were actually retrieved (1.0 when nothing is cited)."""
    invalid = [c for c in cited if c not in set(retrieved)]
    score = 1.0 if not cited else (len(cited) - len(invalid)) / len(cited)
    return {"key": "citation_validity", "score": score, "invalid": invalid}
