"""Versioned prompt files. The active version of each prompt is recorded in trace metadata."""

from __future__ import annotations

from pathlib import Path

PROMPT_DIR = Path(__file__).parent

# Bump the version (and add the new file) whenever a prompt changes; keep old files for diffing.
PROMPT_VERSIONS: dict[str, str] = {
    "sql_generate": "v1",
    "sql_summarize": "v1",
}


def load_prompt(name: str) -> str:
    version = PROMPT_VERSIONS[name]
    return (PROMPT_DIR / f"{name}_{version}.md").read_text(encoding="utf-8")
