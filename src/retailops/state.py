"""Shared, serialisable schemas for evidence and specialist outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

EVIDENCE_SAMPLE_ROWS = 50


class SQLEvidence(BaseModel):
    kind: Literal["sql"] = "sql"
    sql: str
    columns: list[str]
    rows: list[list[Any]]  # capped sample
    row_count: int
    truncated: bool


class SQLAttempt(BaseModel):
    sql: str
    stage: Literal["guard", "execute", "check", "ok"]
    error: str | None = None


class SQLAnalystOutput(BaseModel):
    status: Literal["ok", "empty_result", "failed"]
    answer: str
    confidence_note: str
    evidence: SQLEvidence | None = None
    attempts: list[SQLAttempt]
    lookups: int = 0
