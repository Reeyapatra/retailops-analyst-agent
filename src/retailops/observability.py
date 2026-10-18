"""Structured JSON logging with correlation IDs, and LangSmith run metadata.

Tracing itself is switched on by environment variables (LANGSMITH_TRACING, LANGSMITH_API_KEY,
LANGSMITH_PROJECT); this module only supplies the metadata and tags attached to each run.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import subprocess
import sys
import uuid
from typing import Any

from retailops import __version__
from retailops.config import get_settings
from retailops.prompts import PROMPT_VERSIONS

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")
_logger = logging.getLogger("retailops")


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:16]
    trace_id_var.set(tid)
    return tid


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one JSON log line carrying the current trace id."""
    if not _logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
        _logger.propagate = False
    _logger.log(
        level,
        json.dumps({"event": event, "trace_id": trace_id_var.get(), **fields}, default=str),
    )


@functools.lru_cache(maxsize=1)
def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def run_config(
    component: str,
    *,
    thread_id: str | None = None,
    scenario_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Config dict (run_name, tags, metadata) to pass to graph.invoke / model.invoke."""
    s = get_settings()
    metadata: dict[str, Any] = {
        "prompt_versions": dict(PROMPT_VERSIONS),
        "git_sha": git_sha(),
        "app_version": __version__,
        "app_env": s.app_env,
        "model_ids": {
            "supervisor": s.model_supervisor,
            "sql": s.model_sql,
            "judge": s.model_judge,
        },
        "component": component,
        "trace_id": trace_id_var.get(),
    }
    if thread_id:
        metadata["thread_id"] = thread_id
    if scenario_id:
        metadata["scenario_id"] = scenario_id
    if extra_metadata:
        metadata.update(extra_metadata)
    return {"run_name": component, "tags": [component], "metadata": metadata}
