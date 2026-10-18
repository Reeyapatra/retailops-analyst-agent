"""Warehouse tools: catalog-backed get_schema and guarded, read-only run_sql."""

from __future__ import annotations

import datetime as dt
import decimal
import threading
import time
from pathlib import Path
from typing import Any

import duckdb
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel

from retailops.data.schema import load_catalog
from retailops.guards.injection import spotlight
from retailops.guards.sql_guard import GuardViolation, check_sql
from retailops.observability import log_event

DEFAULT_MAX_ROWS = 1000
DEFAULT_TIMEOUT_S = 10.0


class RunSqlResult(BaseModel):
    ok: bool
    sql_executed: str | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: int = 0
    error_code: str | None = None  # GUARD_REJECTED | TIMEOUT | EXECUTION_ERROR
    error_message: str | None = None
    violations: list[GuardViolation] = []


def get_schema(tables: list[str] | None = None) -> str:
    """Describe warehouse tables and columns in plain English (from schema_catalog.yaml)."""
    catalog = load_catalog()
    if tables:
        wanted = [t.lower() for t in tables]
        unknown = [t for t in wanted if t not in catalog]
        if unknown:
            return f"Unknown tables {unknown}. Available tables: {sorted(catalog)}."
        catalog = {t: catalog[t] for t in wanted}
    lines: list[str] = []
    for name, spec in catalog.items():
        lines.append(
            f"TABLE {name} ({spec['layer']}): {' '.join(str(spec['description']).split())}"
        )
        lines.extend(f"  - {col}: {desc}" for col, desc in spec["columns"].items())
    return "\n".join(lines)


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | float | str):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, dt.date | dt.time):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    return str(v)


class SqlRunner:
    """Runs guarded SQL on a read-only DuckDB connection with a row cap and a timeout."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_rows: int = DEFAULT_MAX_ROWS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.db_path = str(db_path)
        self.max_rows = max_rows
        self.timeout_s = timeout_s
        self.allowed_tables = frozenset(load_catalog())

    def _connect(self) -> duckdb.DuckDBPyConnection:
        # read_only: no writes. enable_external_access=False: no file or URL reads even if the
        # guard were bypassed. Both are defence in depth behind the parser-based guard.
        return duckdb.connect(
            self.db_path, read_only=True, config={"enable_external_access": False}
        )

    def run(self, sql: str) -> RunSqlResult:
        guard = check_sql(sql, allowed_tables=self.allowed_tables, max_rows=self.max_rows + 1)
        if not guard.ok or guard.sql is None:
            log_event(
                "sql_guard_rejected",
                codes=[v.code for v in guard.violations],
                sql=sql[:500],
            )
            return RunSqlResult(
                ok=False,
                error_code="GUARD_REJECTED",
                error_message="; ".join(v.message for v in guard.violations),
                violations=guard.violations,
            )
        return self._execute(guard.sql)

    def _execute(self, sql: str) -> RunSqlResult:
        start = time.perf_counter()
        con = self._connect()
        timer = threading.Timer(self.timeout_s, con.interrupt)
        timer.start()
        try:
            cursor = con.execute(sql)
            columns = [d[0] for d in cursor.description or []]
            fetched = cursor.fetchmany(self.max_rows + 1)
        except duckdb.InterruptException:
            return RunSqlResult(
                ok=False,
                sql_executed=sql,
                error_code="TIMEOUT",
                error_message=f"Query exceeded the {self.timeout_s:g}s time limit.",
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
        except duckdb.Error as e:
            return RunSqlResult(
                ok=False,
                sql_executed=sql,
                error_code="EXECUTION_ERROR",
                error_message=str(e).splitlines()[0][:500],
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
        finally:
            timer.cancel()
            con.close()
        truncated = len(fetched) > self.max_rows
        rows = [[_jsonable(v) for v in r] for r in fetched[: self.max_rows]]
        return RunSqlResult(
            ok=True,
            sql_executed=sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )


def make_sql_tools(runner: SqlRunner) -> list[BaseTool]:
    """LangChain tools (for the single-agent baseline and agents-as-tools later)."""

    @tool
    def get_schema_tool(tables: list[str] | None = None) -> str:
        """Describe warehouse tables and columns. Pass table names, or omit for all tables."""
        return get_schema(tables)

    @tool
    def run_sql(query: str) -> str:
        """Run one read-only SELECT on the retail warehouse. Returns JSON rows or an error."""
        result = runner.run(query)
        return spotlight(result.model_dump_json(), source="run_sql")

    get_schema_tool.name = "get_schema"
    return [get_schema_tool, run_sql]
