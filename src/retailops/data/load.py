"""Build the layered DuckDB warehouse (raw -> staging -> fact -> metrics) from generated data."""

from __future__ import annotations

import csv
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from retailops.data.generate import Event, GeneratedData

RAW_COLUMNS = {
    "event_id": "VARCHAR",
    "event_type": "VARCHAR",
    "event_ts": "TIMESTAMP",
    "ingested_ts": "TIMESTAMP",
    "order_id": "VARCHAR",
    "customer_id": "VARCHAR",
    "product_id": "VARCHAR",
    "quantity": "INTEGER",
    "unit_price": "DOUBLE",
    "payment_amount": "DOUBLE",
    "currency": "VARCHAR",
    "region": "VARCHAR",
    "channel": "VARCHAR",
    "notes": "VARCHAR",
    "schema_version": "INTEGER",
}


def _write_events_csv(events: list[Event], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(Event._fields)
        for e in events:
            w.writerow(
                [
                    "" if v is None else (v.isoformat(sep=" ") if hasattr(v, "isoformat") else v)
                    for v in e
                ]
            )


def _load_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    columns: dict[str, str],
    rows: Sequence[Sequence[Any]],
) -> None:
    """Bulk-load small dimension rows via CSV (executemany is far too slow in DuckDB)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{table}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(columns)
            for r in rows:
                w.writerow([v.isoformat() if hasattr(v, "isoformat") else v for v in r])
        cols = ", ".join(f"'{k}': '{v}'" for k, v in columns.items())
        con.execute(
            f"CREATE TABLE {table} AS SELECT * FROM read_csv(?, header=true, columns={{{cols}}})",
            [str(path)],
        )


def build_warehouse(data: GeneratedData, db_path: Path) -> dict[str, int]:
    """Create (replace) the analytics DuckDB file. Returns row counts per table."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "events.csv"
            _write_events_csv(data.events, csv_path)
            cols = ", ".join(f"'{k}': '{v}'" for k, v in RAW_COLUMNS.items())
            con.execute(
                f"CREATE TABLE raw_events AS SELECT * FROM read_csv(?, header=true, "
                f"columns={{{cols}}}, nullstr='')",
                [str(csv_path)],
            )

        _load_rows(
            con,
            "dim_regions",
            {"region": "VARCHAR", "country": "VARCHAR", "manager": "VARCHAR"},
            data.regions,
        )
        _load_rows(
            con,
            "dim_products",
            {
                "product_id": "VARCHAR",
                "name": "VARCHAR",
                "category": "VARCHAR",
                "base_price": "DOUBLE",
            },
            data.products,
        )
        _load_rows(
            con,
            "dim_customers",
            {
                "customer_id": "VARCHAR",
                "segment": "VARCHAR",
                "signup_date": "DATE",
                "home_region": "VARCHAR",
            },
            data.customers,
        )

        con.execute(
            """
            CREATE TABLE stg_orders AS
            SELECT * EXCLUDE (rn) FROM (
              SELECT *, row_number() OVER (
                PARTITION BY order_id, event_type ORDER BY ingested_ts DESC, event_id
              ) AS rn FROM raw_events
            ) WHERE rn = 1
            """
        )
        con.execute(
            """
            CREATE TABLE fct_orders AS
            WITH o AS (
              SELECT order_id,
                max(customer_id) FILTER (WHERE event_type = 'ORDER_CREATED') AS customer_id,
                max(product_id)  FILTER (WHERE event_type = 'ORDER_CREATED') AS product_id,
                max(region)      FILTER (WHERE event_type = 'ORDER_CREATED') AS region,
                max(channel)     FILTER (WHERE event_type = 'ORDER_CREATED') AS channel,
                max(quantity)    FILTER (WHERE event_type = 'ORDER_CREATED') AS quantity,
                max(unit_price)  FILTER (WHERE event_type = 'ORDER_CREATED') AS unit_price,
                max(event_ts) FILTER (WHERE event_type = 'ORDER_CREATED')     AS created_ts,
                max(event_ts) FILTER (WHERE event_type = 'PAYMENT_COMPLETED') AS paid_ts,
                max(event_ts) FILTER (WHERE event_type = 'ORDER_CANCELLED')   AS cancelled_ts,
                max(event_ts) FILTER (WHERE event_type = 'ORDER_SHIPPED')     AS shipped_ts,
                max(event_ts) FILTER (WHERE event_type = 'ORDER_DELIVERED')   AS delivered_ts,
                max(payment_amount) FILTER (WHERE event_type = 'PAYMENT_COMPLETED') AS paid_amount
              FROM stg_orders GROUP BY order_id
            )
            SELECT o.order_id, o.customer_id, o.product_id, p.category, o.region, o.channel,
              o.quantity, o.unit_price, o.created_ts, o.paid_ts, o.cancelled_ts, o.shipped_ts,
              o.delivered_ts,
              CASE WHEN o.cancelled_ts IS NOT NULL THEN 'cancelled'
                   WHEN o.delivered_ts IS NOT NULL THEN 'delivered'
                   WHEN o.shipped_ts IS NOT NULL THEN 'shipped'
                   WHEN o.paid_ts IS NOT NULL THEN 'paid'
                   ELSE 'created' END AS status,
              coalesce(o.paid_amount, 0) AS gross_revenue,
              CASE WHEN o.cancelled_ts IS NULL THEN coalesce(o.paid_amount, 0) ELSE 0 END
                AS net_revenue,
              round(date_diff('second', o.shipped_ts, o.delivered_ts) / 86400.0, 2)
                AS delivery_days
            FROM o LEFT JOIN dim_products p USING (product_id)
            """
        )
        con.execute(
            """
            CREATE TABLE daily_metrics AS
            SELECT cast(created_ts AS DATE) AS metric_date, region, category,
              count(*) AS orders,
              count(paid_ts) AS paid_orders,
              round(sum(gross_revenue), 2) AS gross_revenue,
              round(sum(net_revenue), 2) AS net_revenue,
              count(cancelled_ts) AS cancelled_orders,
              round(sum(gross_revenue) / nullif(count(paid_ts), 0), 2) AS aov
            FROM fct_orders GROUP BY 1, 2, 3
            """
        )
        con.execute(
            """
            CREATE TABLE dq_check_results (
              run_id VARCHAR, check_name VARCHAR, run_ts TIMESTAMP, params VARCHAR,
              status VARCHAR, finding_count BIGINT, sample_ids VARCHAR
            )
            """
        )
        tables = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main' "
                "ORDER BY table_name"
            ).fetchall()
        ]
        counts: dict[str, int] = {}
        for t in tables:
            row = con.execute(f"SELECT count(*) FROM {t}").fetchone()
            counts[t] = int(row[0]) if row else 0
    finally:
        con.close()
    return counts


def init_audit_db(path: Path) -> None:
    """Create the separate, writable audit database (not the read-only analytics file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS action_audit_log (
              proposal_id VARCHAR, action_type VARCHAR, target VARCHAR, rationale VARCHAR,
              status VARCHAR, actor VARCHAR, decided_ts TIMESTAMP, trace_id VARCHAR
            )
            """
        )
    finally:
        con.close()
