"""Parser-based SQL guard (rules: docs/05-SECURITY-AND-GUARDRAILS.md section 2).

The guard parses with sqlglot (DuckDB dialect), applies the rules below, and returns a
*regenerated* SQL string built from the syntax tree. Callers must execute that string, never
the original text, so comments or odd formatting in the input cannot reach the database.

Known limits (also stated in the write-up): the guard is only as good as the parser. A dialect
edge case the parser misreads could slip through, which is why the analytics connection is also
read-only with external access disabled. Unknown (non-allowlisted) functions are rejected, which
can block some legitimate queries; the rejection reason is returned to the agent so it can retry.
"""

from __future__ import annotations

from collections.abc import Collection

import sqlglot
from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_SQL_CHARS = 10_000
DEFAULT_MAX_ROWS = 1000

# Functions sqlglot does not model as typed nodes but that analysts legitimately use.
# Everything else that parses as an "anonymous" function is rejected.
ALLOWED_ANONYMOUS_FUNCTIONS = frozenset(
    {
        "epoch",
        "epoch_ms",
        "strftime",
        "strptime",
        "dayname",
        "monthname",
        "isodow",
        "yearweek",
        "weekofyear",
        "make_date",
        "last_day",
        "time_bucket",
        "median",
        "mode",
        "quantile_cont",
        "quantile_disc",
        "arg_max",
        "arg_min",
        "max_by",
        "min_by",
        "bool_and",
        "bool_or",
        "string_agg",
        "date_diff",
        "datediff",
        "date_sub",
        "date_part",
        "greatest",
        "least",
        "round_even",
        "isfinite",
        "list_sort",
        "list_distinct",
    }
)

_FORBIDDEN_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Create",
    "Drop",
    "Alter",
    "Command",
    "Copy",
    "Attach",
    "Detach",
    "Pragma",
    "Set",
    "Use",
    "Install",
    "Merge",
    "Transaction",
    "Commit",
    "Rollback",
    "Grant",
    "TruncateTable",
    "Describe",
    "Show",
    "Summarize",
    "Export",
    "LoadData",
)
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    cls for name in _FORBIDDEN_NODE_NAMES if isinstance(cls := getattr(exp, name, None), type)
)
_ROOT_TYPES = (exp.Select, exp.SetOperation)


class GuardViolation(BaseModel):
    code: str
    message: str


class GuardResult(BaseModel):
    ok: bool
    sql: str | None = None
    violations: list[GuardViolation] = []


def _reject(*violations: GuardViolation) -> GuardResult:
    return GuardResult(ok=False, sql=None, violations=list(violations))


def check_sql(
    sql: str,
    *,
    allowed_tables: Collection[str],
    max_rows: int = DEFAULT_MAX_ROWS,
) -> GuardResult:
    """Validate one SQL string. On success `result.sql` is the normalized, LIMIT-capped query."""
    if not sql or not sql.strip().strip(";").strip():
        return _reject(GuardViolation(code="EMPTY", message="The SQL statement is empty."))
    if len(sql) > MAX_SQL_CHARS:
        return _reject(
            GuardViolation(code="TOO_LONG", message=f"SQL exceeds {MAX_SQL_CHARS} characters.")
        )

    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except SqlglotError as e:
        return _reject(GuardViolation(code="PARSE_ERROR", message=f"Could not parse SQL: {e}"))
    if not statements:
        return _reject(GuardViolation(code="EMPTY", message="The SQL statement is empty."))
    if len(statements) > 1:
        return _reject(
            GuardViolation(code="MULTIPLE_STATEMENTS", message="Exactly one statement is allowed.")
        )

    tree = statements[0]
    if not isinstance(tree, _ROOT_TYPES):
        return _reject(
            GuardViolation(
                code="NOT_SELECT",
                message="Only SELECT queries (optionally with WITH CTEs) are allowed.",
            )
        )

    violations: list[GuardViolation] = []

    forbidden = {type(n).__name__ for n in tree.walk() if isinstance(n, _FORBIDDEN_NODES)}
    if forbidden:
        violations.append(
            GuardViolation(
                code="NOT_SELECT",
                message=f"Statement types not allowed inside a query: {sorted(forbidden)}.",
            )
        )

    allowed = {t.lower() for t in allowed_tables}
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            violations.append(
                GuardViolation(
                    code="TABLE_FUNCTION",
                    message="Table functions and file/URL sources are not allowed in FROM.",
                )
            )
            continue
        if table.args.get("db") or table.args.get("catalog"):
            violations.append(
                GuardViolation(
                    code="TABLE_NOT_ALLOWED",
                    message=f"Schema-qualified table '{table.sql()}' is not allowed; "
                    "use the plain table name.",
                )
            )
            continue
        name = table.name.lower()
        if name not in allowed and name not in cte_names:
            violations.append(
                GuardViolation(
                    code="TABLE_NOT_ALLOWED",
                    message=f"Table '{table.name}' is not in the allowed warehouse tables: "
                    f"{sorted(allowed)}.",
                )
            )
    if any(isinstance(n, exp.Unnest) for n in tree.walk()):
        violations.append(
            GuardViolation(code="TABLE_FUNCTION", message="UNNEST sources are not allowed.")
        )

    for func in tree.find_all(exp.Func):
        if isinstance(func, exp.Anonymous):
            fname = str(func.name).lower()
            if fname not in ALLOWED_ANONYMOUS_FUNCTIONS and not isinstance(func.parent, exp.Table):
                violations.append(
                    GuardViolation(
                        code="FUNCTION_NOT_ALLOWED",
                        message=f"Function '{func.name}' is not on the allowed function list.",
                    )
                )
        elif type(func).__name__.startswith("Read"):
            violations.append(
                GuardViolation(
                    code="FUNCTION_NOT_ALLOWED",
                    message=f"File-reading function '{type(func).__name__}' is not allowed.",
                )
            )

    if violations:
        # dedupe identical messages while keeping order
        seen: set[tuple[str, str]] = set()
        unique = []
        for v in violations:
            if (v.code, v.message) not in seen:
                seen.add((v.code, v.message))
                unique.append(v)
        return _reject(*unique)

    return GuardResult(
        ok=True, sql=_apply_limit(tree, max_rows).sql(dialect="duckdb", comments=False)
    )


def _apply_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
    existing = tree.args.get("limit")
    if existing is not None:
        value = existing.args.get("expression")
        if isinstance(value, exp.Literal) and value.is_int and int(value.name) <= max_rows:
            return tree
    return tree.limit(max_rows)  # type: ignore[attr-defined, no-any-return]
