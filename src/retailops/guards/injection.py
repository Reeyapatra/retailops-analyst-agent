"""Prompt-injection defences for untrusted text (tool output, retrieved documents, table data).

`spotlight` is the primary defence: untrusted text is delimited and the model is told it is
data. `looks_like_instruction` is only a *signal* for logging and evals, never the only control.
Permissions never depend on anything found in data.
"""

from __future__ import annotations

import re

_TAG = "untrusted_data"

_INSTRUCTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules)",
        r"(reveal|show|print) (your |the )?(system )?prompt",
        r"system override",
        r"disable (the )?(sql )?guard",
        r"\bdrop\s+table\b",
        r"approve (every|all) (pending )?action",
        r"you are now\b",
        r"developer mode",
    )
]


def looks_like_instruction(text: str) -> bool:
    return any(p.search(text) for p in _INSTRUCTION_PATTERNS)


def spotlight(text: str, source: str) -> str:
    """Wrap untrusted text in delimiters; neutralise any attempt to close the block early."""
    safe = text.replace(f"</{_TAG}", f"<\\/{_TAG}").replace(f"<{_TAG}", f"<\\{_TAG}")
    return f'<{_TAG} source="{source}">\n{safe}\n</{_TAG}>'
