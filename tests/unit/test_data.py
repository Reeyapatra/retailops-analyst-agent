"""M1 tests: determinism, sane shapes, and that every injected anomaly is detectable by a
plain SQL query against the warehouse and agrees with the ground-truth manifest."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest

from retailops.data.generate import GenConfig, generate, main
from retailops.data.load import build_warehouse, init_audit_db
from retailops.data.schema import load_catalog

DAYS = 180
OPD = 100


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, dict[str, Any]]]:
    tmp = tmp_path_factory.mktemp("wh")
    data = generate(GenConfig(seed=42, days=DAYS, orders_per_day=OPD))
    db = tmp / "retailops.duckdb"
    build_warehouse(data, db)
    yield db, data.manifest


@pytest.fixture
def con(built: tuple[Path, dict[str, Any]]) -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(str(built[0]), read_only=True)
    yield c
    c.close()


def anomaly(manifest: dict[str, Any], kind: str) -> dict[str, Any]:
    found = [a for a in manifest["anomalies"] if a["type"] == kind]
    assert len(found) == 1, kind
    return found[0]


def one(con: duckdb.DuckDBPyConnection, sql: str, *params: Any) -> Any:
    row = con.execute(sql, list(params)).fetchone()
    assert row is not None
    return row[0]


# ---------- determinism ----------


def _fingerprint(db: Path) -> tuple[Any, ...]:
    c = duckdb.connect(str(db), read_only=True)
    try:
        return tuple(
            c.execute(
                "SELECT count(*), sum(payment_amount), "
                "md5(string_agg(event_id || cast(ingested_ts AS VARCHAR), ',' ORDER BY event_id)) "
                "FROM raw_events"
            ).fetchone()
            or ()
        )
    finally:
        c.close()


def test_generation_is_deterministic(tmp_path: Path) -> None:
    cfg = GenConfig(seed=7, days=DAYS, orders_per_day=15)
    a, b = generate(cfg), generate(cfg)
    assert a.events == b.events
    assert a.manifest == b.manifest
    build_warehouse(a, tmp_path / "a.duckdb")
    build_warehouse(b, tmp_path / "b.duckdb")
    assert _fingerprint(tmp_path / "a.duckdb") == _fingerprint(tmp_path / "b.duckdb")


def test_different_seed_differs() -> None:
    a = generate(GenConfig(seed=1, days=DAYS, orders_per_day=10))
    b = generate(GenConfig(seed=2, days=DAYS, orders_per_day=10))
    assert a.events != b.events


def test_anomalies_need_enough_days() -> None:
    with pytest.raises(ValueError):
        generate(GenConfig(days=30))


# ---------- shape and sanity ----------


def test_row_counts_and_rates(con: duckdb.DuckDBPyConnection) -> None:
    orders = one(con, "SELECT count(*) FROM fct_orders")
    assert 0.8 * DAYS * OPD < orders < 1.15 * DAYS * OPD
    events = one(con, "SELECT count(*) FROM raw_events")
    assert 2.5 < events / orders < 5.5
    cancel_rate = one(con, "SELECT count(cancelled_ts)::DOUBLE / count(paid_ts) FROM fct_orders")
    assert 0.02 < cancel_rate < 0.08


def test_referential_integrity(con: duckdb.DuckDBPyConnection) -> None:
    assert (
        one(
            con,
            "SELECT count(*) FROM fct_orders WHERE product_id NOT IN "
            "(SELECT product_id FROM dim_products)",
        )
        == 0
    )
    assert (
        one(
            con,
            "SELECT count(*) FROM fct_orders WHERE region NOT IN (SELECT region FROM dim_regions)",
        )
        == 0
    )


def test_weekend_seasonality(con: duckdb.DuckDBPyConnection) -> None:
    # Saturdays have a higher demand factor than Mondays; compare outside anomaly windows.
    sql = (
        "SELECT avg(n) FROM (SELECT cast(created_ts AS DATE) d, count(*) n FROM fct_orders "
        "WHERE category <> 'Apparel' GROUP BY d) WHERE dayofweek(d) = ? "
        "AND d < DATE '2026-03-01'"
    )
    assert one(con, sql, 6) > one(con, sql, 1)


def test_fct_orders_lifecycle_consistent(con: duckdb.DuckDBPyConnection) -> None:
    assert one(con, "SELECT count(*) FROM fct_orders WHERE shipped_ts < paid_ts") == 0
    assert one(con, "SELECT count(*) FROM fct_orders WHERE delivered_ts < shipped_ts") == 0
    assert (
        one(
            con,
            "SELECT count(*) FROM fct_orders WHERE cancelled_ts IS NOT NULL "
            "AND shipped_ts IS NOT NULL",
        )
        == 0
    )


def test_daily_metrics_matches_fct(con: duckdb.DuckDBPyConnection) -> None:
    assert one(con, "SELECT sum(orders) FROM daily_metrics") == one(
        con, "SELECT count(*) FROM fct_orders"
    )
    a = one(con, "SELECT sum(net_revenue) FROM daily_metrics")
    b = one(con, "SELECT sum(net_revenue) FROM fct_orders")
    assert abs(a - b) < 1.0


# ---------- each anomaly is detectable ----------


def test_duplicate_events(con: duckdb.DuckDBPyConnection, built: Any) -> None:
    a = anomaly(built[1], "duplicate_events")
    dup_groups = """
        SELECT order_id, event_type, count(*) c FROM raw_events
        GROUP BY 1, 2 HAVING c > 1
    """
    n_dupes = one(con, f"SELECT coalesce(sum(c - 1), 0) FROM ({dup_groups})")
    assert n_dupes == a["magnitude"]["duplicate_events_added"] > 0
    # all duplicates are PAYMENT_COMPLETED on the anomaly day, and raw revenue is inflated
    outside = one(
        con,
        f"""SELECT count(*) FROM raw_events r JOIN ({dup_groups}) d USING (order_id, event_type)
            WHERE r.event_type <> 'PAYMENT_COMPLETED' OR cast(r.event_ts AS DATE) <> ?""",
        date.fromisoformat(a["start"]),
    )
    assert outside == 0
    raw_rev = one(
        con,
        "SELECT sum(payment_amount) FROM raw_events "
        "WHERE event_type='PAYMENT_COMPLETED' AND cast(event_ts AS DATE)=?",
        date.fromisoformat(a["start"]),
    )
    stg_rev = one(
        con,
        "SELECT sum(payment_amount) FROM stg_orders "
        "WHERE event_type='PAYMENT_COMPLETED' AND cast(event_ts AS DATE)=?",
        date.fromisoformat(a["start"]),
    )
    assert raw_rev > 1.8 * stg_rev


def test_late_arriving_events(con: duckdb.DuckDBPyConnection, built: Any) -> None:
    a = anomaly(built[1], "late_arriving_events")
    late = "ingested_ts - event_ts > INTERVAL 20 HOUR"
    assert (
        one(con, f"SELECT count(*) FROM raw_events WHERE {late}")
        == (a["magnitude"]["events_delayed"])
    )
    rows = con.execute(f"SELECT DISTINCT region FROM raw_events WHERE {late}").fetchall()
    assert rows == [(a["scope"]["region"],)]
    # outside the West window the only event arriving over 10 minutes late is a replayed
    # duplicate payment (6h lag), so late arrival is separable from the replay anomaly
    others = one(
        con,
        f"SELECT count(*) FROM raw_events WHERE NOT ({late}) "
        "AND ingested_ts - event_ts > INTERVAL 10 MINUTE AND event_type <> 'PAYMENT_COMPLETED'",
    )
    assert others == 0


def test_cancellation_spike(con: duckdb.DuckDBPyConnection, built: Any) -> None:
    a = anomaly(built[1], "cancellation_spike")
    s, e = a["start"], a["end"]
    q = (
        "SELECT count(cancelled_ts)::DOUBLE / count(paid_ts) FROM fct_orders "
        "WHERE category = ? AND region = ? AND cast(created_ts AS DATE) BETWEEN ? AND ?"
    )
    in_scope = one(con, q, a["scope"]["category"], a["scope"]["region"], s, e)
    before = one(con, q, a["scope"]["category"], a["scope"]["region"], "2026-01-01", "2026-03-31")
    other = one(con, q, "Home", a["scope"]["region"], s, e)
    assert in_scope > 0.2 and before < 0.1 and other < 0.1


def test_real_revenue_drop_without_dq_problem(con: duckdb.DuckDBPyConnection, built: Any) -> None:
    a = anomaly(built[1], "revenue_drop_real")
    s, e = date.fromisoformat(a["start"]), date.fromisoformat(a["end"])
    q = (
        "SELECT avg(n) FROM (SELECT cast(created_ts AS DATE) d, count(*) n FROM fct_orders "
        "WHERE category = ? GROUP BY d) WHERE d BETWEEN ? AND ?"
    )
    cat = a["scope"]["category"]
    during = one(con, q, cat, s, e)
    before = one(con, q, cat, date(2026, 4, 1), date(2026, 4, 20))
    assert during < 0.7 * before
    # false-alarm resistance: no data-quality symptom inside this window
    assert_clean_window(con, a["start"], a["end"])


def test_invalid_payments(con: duckdb.DuckDBPyConnection, built: Any) -> None:
    a = anomaly(built[1], "invalid_payments")
    n = one(
        con,
        "SELECT count(*) FROM raw_events WHERE event_type='PAYMENT_COMPLETED' "
        "AND payment_amount <= 0",
    )
    assert n == a["magnitude"]["invalid_payment_events"] > 0
    outside = one(
        con,
        "SELECT count(*) FROM raw_events WHERE payment_amount <= 0 "
        "AND cast(event_ts AS DATE) NOT BETWEEN ? AND ?",
        date.fromisoformat(a["start"]),
        date.fromisoformat(a["end"]),
    )
    assert outside == 0


def test_missing_shipments(con: duckdb.DuckDBPyConnection, built: Any) -> None:
    a = anomaly(built[1], "missing_shipments")
    q = """SELECT region, count(*) FROM fct_orders
           WHERE paid_ts IS NOT NULL AND cancelled_ts IS NULL AND shipped_ts IS NULL
             AND paid_ts < TIMESTAMP '2026-06-01'
           GROUP BY region"""
    rows = con.execute(q).fetchall()
    assert rows == [(a["scope"]["region"], a["magnitude"]["orders_without_shipment"])]
    assert a["magnitude"]["orders_without_shipment"] > 0


def test_schema_drift(con: duckdb.DuckDBPyConnection, built: Any) -> None:
    a = anomaly(built[1], "schema_drift")
    s, e = date.fromisoformat(a["start"]), date.fromisoformat(a["end"])
    assert (
        one(con, "SELECT count(*) FROM raw_events WHERE currency IS NULL")
        == (a["magnitude"]["events_affected"])
    )
    assert (
        one(con, "SELECT count(*) FROM raw_events WHERE schema_version = 2")
        == (a["magnitude"]["events_affected"])
    )
    assert (
        one(
            con,
            "SELECT count(*) FROM raw_events WHERE currency IS NULL "
            "AND cast(event_ts AS DATE) NOT BETWEEN ? AND ?",
            s,
            e,
        )
        == 0
    )


def assert_clean_window(con: duckdb.DuckDBPyConnection, start: str, end: str) -> None:
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    in_window = "cast(event_ts AS DATE) BETWEEN ? AND ?"
    assert (
        one(
            con,
            f"SELECT count(*) FROM (SELECT 1 FROM raw_events WHERE {in_window} "
            "GROUP BY order_id, event_type HAVING count(*) > 1)",
            s,
            e,
        )
        == 0
    )
    assert (
        one(
            con,
            f"SELECT count(*) FROM raw_events WHERE {in_window} "
            "AND (ingested_ts - event_ts > INTERVAL 10 MINUTE OR currency IS NULL "
            "OR payment_amount <= 0)",
            s,
            e,
        )
        == 0
    )


def test_decoys_are_clean(con: duckdb.DuckDBPyConnection, built: Any) -> None:
    decoys = [a for a in built[1]["anomalies"] if a["type"] == "decoy"]
    assert len(decoys) >= 2
    for d in decoys:
        assert d["root_cause"] == "none"
        assert_clean_window(con, d["start"], d["end"])
        rate = one(
            con,
            "SELECT count(cancelled_ts)::DOUBLE / count(paid_ts) FROM fct_orders "
            "WHERE cast(created_ts AS DATE) BETWEEN ? AND ?",
            date.fromisoformat(d["start"]),
            date.fromisoformat(d["end"]),
        )
        assert rate < 0.1


def test_manifest_schema(built: Any) -> None:
    for a in built[1]["anomalies"]:
        assert {
            "anomaly_id",
            "type",
            "start",
            "end",
            "scope",
            "magnitude",
            "root_cause",
            "expected_findings",
        } <= set(a)
    ids = [a["anomaly_id"] for a in built[1]["anomalies"]]
    assert len(ids) == len(set(ids))


# ---------- injection fixtures ----------


def test_injection_fixtures_are_inert_data(con: duckdb.DuckDBPyConnection, built: Any) -> None:
    fixtures = built[1]["injection_fixtures"]
    assert len(fixtures) >= 3
    in_notes = one(con, "SELECT count(*) FROM raw_events WHERE notes LIKE '%INJECTION_FIXTURE%'")
    assert in_notes == sum(1 for f in fixtures if f["table"] == "raw_events")
    assert one(con, "SELECT count(*) FROM dim_products WHERE name LIKE '%INJECTION_FIXTURE%'") == 1
    # the fixture text did not alter the warehouse
    assert (
        one(con, "SELECT count(*) FROM information_schema.tables WHERE table_name = 'raw_events'")
        == 1
    )


# ---------- catalog, audit DB, CLI ----------


def test_catalog_matches_warehouse(con: duckdb.DuckDBPyConnection) -> None:
    catalog = load_catalog()
    actual: dict[str, set[str]] = {}
    for table, column in con.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'main'"
    ).fetchall():
        actual.setdefault(table, set()).add(column)
    assert set(catalog) == set(actual)
    for table, spec in catalog.items():
        assert spec["description"].strip(), table
        assert set(spec["columns"]) == actual[table], table
        assert all(str(d).strip() for d in spec["columns"].values()), table


def test_audit_db_is_separate(tmp_path: Path) -> None:
    audit = tmp_path / "audit.duckdb"
    init_audit_db(audit)
    c = duckdb.connect(str(audit))
    tables = {
        r[0] for r in c.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    c.close()
    assert tables == {"action_audit_log"}


def test_analytics_connection_cannot_write(built: Any) -> None:
    c = duckdb.connect(str(built[0]), read_only=True)
    with pytest.raises(duckdb.Error):
        c.execute("DELETE FROM raw_events")
    c.close()


def test_cli_writes_outputs(tmp_path: Path) -> None:
    db, manifest, audit = tmp_path / "w.duckdb", tmp_path / "m.json", tmp_path / "a.duckdb"
    rc = main(
        [
            "--seed",
            "3",
            "--days",
            "180",
            "--orders-per-day",
            "10",
            "--db",
            str(db),
            "--manifest",
            str(manifest),
            "--audit-db",
            str(audit),
        ]
    )
    assert rc == 0
    assert db.exists() and audit.exists()
    assert json.loads(manifest.read_text())["seed"] == 3
