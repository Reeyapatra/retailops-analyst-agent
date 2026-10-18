"""SQL guard tests (written first). Rules come from docs/05-SECURITY-AND-GUARDRAILS.md section 2."""

from __future__ import annotations

import pytest

from retailops.guards.sql_guard import check_sql

TABLES = {"raw_events", "stg_orders", "fct_orders", "dim_products", "daily_metrics"}


def codes(sql: str) -> set[str]:
    return {v.code for v in check_sql(sql, allowed_tables=TABLES).violations}


# ---------- accepted ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM fct_orders",
        "select region, sum(net_revenue) from fct_orders group by region order by 2 desc",
        "WITH a AS (SELECT * FROM fct_orders) SELECT count(*) FROM a",
        "WITH RECURSIVE r AS (SELECT 1 n UNION ALL SELECT n + 1 FROM r WHERE n < 5) "
        "SELECT * FROM r",
        "SELECT 1 UNION SELECT 2",
        "FROM fct_orders SELECT count(*)",
        "SELECT * FROM fct_orders o JOIN dim_products p USING (product_id)",
        "SELECT count(*) FROM (SELECT DISTINCT customer_id FROM fct_orders) t",
        "SELECT date_trunc('month', created_ts) m, count(*) FROM fct_orders GROUP BY 1",
        "SELECT rank() OVER (PARTITION BY region ORDER BY net_revenue DESC) FROM fct_orders",
        "SELECT 1;",  # trailing semicolon is one statement
        "SELECT 1 -- DROP TABLE fct_orders",  # comment text is not executed
        "SELECT 'DROP TABLE fct_orders' AS s",  # string literal, not a statement
    ],
)
def test_accepts_read_only_queries(sql: str) -> None:
    result = check_sql(sql, allowed_tables=TABLES)
    assert result.ok, result.violations
    assert result.sql is not None


# ---------- rejected: statement type ----------


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DROP TABLE fct_orders", "NOT_SELECT"),
        ("DELETE FROM fct_orders", "NOT_SELECT"),
        ("UPDATE fct_orders SET region = 'X'", "NOT_SELECT"),
        ("INSERT INTO fct_orders SELECT * FROM fct_orders", "NOT_SELECT"),
        ("CREATE TABLE t AS SELECT 1", "NOT_SELECT"),
        ("CREATE VIEW v AS SELECT 1", "NOT_SELECT"),
        ("ALTER TABLE fct_orders ADD COLUMN x INT", "NOT_SELECT"),
        ("TRUNCATE fct_orders", "NOT_SELECT"),
        ("COPY fct_orders TO 'out.csv'", "NOT_SELECT"),
        ("ATTACH 'other.db' AS o", "NOT_SELECT"),
        ("DETACH o", "NOT_SELECT"),
        ("INSTALL httpfs", "NOT_SELECT"),
        ("LOAD httpfs", "NOT_SELECT"),
        ("PRAGMA database_list", "NOT_SELECT"),
        ("SET memory_limit = '1GB'", "NOT_SELECT"),
        ("CALL pragma_version()", "NOT_SELECT"),
        ("EXPLAIN SELECT 1", "NOT_SELECT"),
        ("DESCRIBE fct_orders", "NOT_SELECT"),
        ("SHOW TABLES", "NOT_SELECT"),
        ("GRANT ALL ON fct_orders TO x", "NOT_SELECT"),
        ("BEGIN TRANSACTION", "NOT_SELECT"),
    ],
)
def test_rejects_non_select_statements(sql: str, code: str) -> None:
    assert code in codes(sql)


def test_rejects_statements_the_parser_does_not_understand() -> None:
    # the parser fails on this DuckDB statement; failing closed is the required behaviour
    r = check_sql("EXPORT DATABASE 'dir'", allowed_tables=TABLES)
    assert not r.ok and r.sql is None


def test_rejects_multiple_statements() -> None:
    assert "MULTIPLE_STATEMENTS" in codes("SELECT 1; DROP TABLE fct_orders")
    assert "MULTIPLE_STATEMENTS" in codes("SELECT 1; SELECT 2")


def test_rejects_empty_unparseable_and_oversized() -> None:
    assert "EMPTY" in codes("")
    assert "EMPTY" in codes("   ;  ")
    assert "PARSE_ERROR" in codes("SELECT * FROM (")
    assert "TOO_LONG" in codes("SELECT 1 " + "+ 1 " * 5000)


# ---------- rejected: tables and functions ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers_secret",
        "SELECT * FROM dim_customers",  # real table, but not in this allowlist
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM main.fct_orders",  # qualified names are not allowed
        "SELECT * FROM memory.main.fct_orders",
        "SELECT * FROM fct_orders JOIN secret ON true",
        "SELECT * FROM (SELECT * FROM audit_log) t",
        "SELECT (SELECT count(*) FROM audit_log)",
        "SELECT * FROM fct_orders WHERE order_id IN (SELECT order_id FROM audit_log)",
        "SELECT * FROM 'data/retailops.duckdb'",
        'SELECT * FROM "data/secrets.csv"',
    ],
)
def test_rejects_tables_outside_allowlist(sql: str) -> None:
    assert "TABLE_NOT_ALLOWED" in codes(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('x.csv')",
        "SELECT * FROM read_parquet('x.parquet')",
        "SELECT * FROM read_json_auto('x.json')",
        "SELECT * FROM glob('*')",
        "SELECT * FROM duckdb_tables()",
        "SELECT * FROM range(10)",
        "SELECT * FROM fct_orders, read_csv('x.csv')",
        "SELECT * FROM unnest([1, 2])",
    ],
)
def test_rejects_table_functions(sql: str) -> None:
    assert "TABLE_FUNCTION" in codes(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT getenv('HOME')",
        "SELECT read_text('secret.txt')",
        "SELECT read_blob('secret.bin')",
        "SELECT current_setting('home_directory')",
        "SELECT * FROM fct_orders WHERE region = getenv('X')",
        "SELECT query('select 1')",
        "SELECT load_extension('x')",
    ],
)
def test_rejects_dangerous_scalar_functions(sql: str) -> None:
    assert "FUNCTION_NOT_ALLOWED" in codes(sql)


def test_cte_names_do_not_widen_the_allowlist() -> None:
    # a CTE may be named anything, but its body must still use allowed tables
    assert "TABLE_NOT_ALLOWED" in codes("WITH x AS (SELECT * FROM audit_log) SELECT * FROM x")
    # shadowing an allowed table name with a CTE is harmless
    assert check_sql(
        "WITH fct_orders AS (SELECT 1 AS a) SELECT * FROM fct_orders", allowed_tables=TABLES
    ).ok


def test_table_names_are_case_insensitive() -> None:
    assert check_sql("SELECT count(*) FROM FCT_ORDERS", allowed_tables=TABLES).ok


# ---------- limit enforcement and normalization ----------


def test_adds_limit_when_missing() -> None:
    r = check_sql("SELECT * FROM fct_orders", allowed_tables=TABLES, max_rows=100)
    assert r.sql is not None and r.sql.upper().endswith("LIMIT 100")


def test_lowers_excessive_limit() -> None:
    r = check_sql("SELECT * FROM fct_orders LIMIT 999999", allowed_tables=TABLES, max_rows=100)
    assert r.sql is not None and r.sql.upper().endswith("LIMIT 100")


def test_keeps_smaller_limit() -> None:
    r = check_sql("SELECT * FROM fct_orders LIMIT 5", allowed_tables=TABLES, max_rows=100)
    assert r.sql is not None and r.sql.upper().endswith("LIMIT 5")


def test_limit_applies_to_set_operations() -> None:
    r = check_sql("SELECT 1 UNION ALL SELECT 2", allowed_tables=TABLES, max_rows=100)
    assert r.sql is not None and r.sql.upper().endswith("LIMIT 100")


def test_returns_normalized_sql_without_comments() -> None:
    r = check_sql("SELECT 1 /* hidden */ -- DROP TABLE x", allowed_tables=TABLES)
    assert r.sql is not None and "DROP" not in r.sql and "hidden" not in r.sql


def test_rejection_has_no_sql_and_readable_messages() -> None:
    r = check_sql("DROP TABLE fct_orders", allowed_tables=TABLES)
    assert not r.ok and r.sql is None
    assert all(v.message for v in r.violations)
