"""Deterministic data-quality checks and metric scanners, tested against the injected anomalies
in the full default dataset. Every anomaly must be found with the right kind, scope and window;
decoy windows and clean periods must produce no findings."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from retailops.tools.dq import DQ_CHECKS, DqRunner, Finding
from retailops.tools.metrics import MetricScanner, binom_sf


@pytest.fixture(scope="module")
def dq(full_db: Path) -> DqRunner:
    return DqRunner(full_db)


@pytest.fixture(scope="module")
def manifest(full_data: Any) -> dict[str, Any]:
    return {a["anomaly_id"]: a for a in full_data.manifest["anomalies"]}


def d(s: str) -> date:
    return date.fromisoformat(s)


def overlaps(f: Finding, a: dict[str, Any]) -> bool:
    return f.start <= d(a["end"]) and d(a["start"]) <= f.end


def test_unknown_checks_and_bad_params_are_rejected(dq: DqRunner) -> None:
    with pytest.raises(ValueError, match="unknown check"):
        dq.run_check("drop_everything")
    with pytest.raises(ValueError):
        dq.run_check("late_events", region="Mars")
    with pytest.raises(ValueError):
        dq.run_check("late_events", min_lag_minutes=0)
    with pytest.raises(ValueError):
        dq.run_check("null_fields", columns=["notes; DROP TABLE raw_events"])
    assert len(DQ_CHECKS) == 7


def test_duplicate_events_found_exactly(dq: DqRunner, manifest: dict[str, Any]) -> None:
    a = manifest["A1"]
    res = dq.run_check("duplicate_events")
    assert res.status == "fail" and len(res.findings) == 1
    f = res.findings[0]
    assert (f.start, f.end) == (d(a["start"]), d(a["end"]))
    assert f.count == a["magnitude"]["duplicate_events_added"]
    assert f.scope == {"event_type": "PAYMENT_COMPLETED"} and f.sample_ids and f.sql


def test_late_events_found_for_the_right_region(dq: DqRunner, manifest: dict[str, Any]) -> None:
    a = manifest["A2"]
    res = dq.run_check("late_events")
    assert [f.scope for f in res.findings] == [{"region": "West"}]
    f = res.findings[0]
    assert overlaps(f, a) and f.start >= d(a["start"]) and f.end <= d(a["end"])
    assert f.count == a["magnitude"]["events_delayed"]


def test_invalid_payments_found(dq: DqRunner, manifest: dict[str, Any]) -> None:
    a = manifest["A5"]
    f = dq.run_check("invalid_payments").findings
    assert len(f) == 1 and f[0].count == a["magnitude"]["invalid_payment_events"]
    assert f[0].start >= d(a["start"]) and f[0].end <= d(a["end"])


def test_missing_shipments_found_for_the_right_region(
    dq: DqRunner, manifest: dict[str, Any]
) -> None:
    a = manifest["A6"]
    f = dq.run_check("missing_shipments").findings
    assert [x.scope for x in f] == [{"region": "East"}]
    assert f[0].count == a["magnitude"]["orders_without_shipment"]


def test_schema_drift_and_null_currency_found(dq: DqRunner, manifest: dict[str, Any]) -> None:
    a = manifest["A7"]
    drift = dq.run_check("schema_drift").findings
    nulls = dq.run_check("null_fields", columns=["currency"]).findings
    for f in (*drift, *nulls):
        assert (f.start, f.end) == (d(a["start"]), d(a["end"]))
        assert f.count == a["magnitude"]["events_affected"]
    assert len(drift) == 1 and len(nulls) == 1 and nulls[0].scope == {"field": "currency"}


def test_invalid_sequences_absent_in_clean_generator(dq: DqRunner) -> None:
    res = dq.run_check("invalid_sequence")
    assert res.status == "pass" and res.findings == []


def test_date_filter_restricts_findings(dq: DqRunner) -> None:
    res = dq.run_check("invalid_payments", start=date(2026, 1, 1), end=date(2026, 3, 1))
    assert res.status == "pass" and res.finding_count == 0


def test_decoy_windows_are_clean(dq: DqRunner, manifest: dict[str, Any]) -> None:
    findings = [f for r in dq.run_all() for f in r.findings]
    for decoy in (manifest["D1"], manifest["D2"]):
        assert [f for f in findings if overlaps(f, decoy)] == []


def test_every_finding_matches_an_injected_anomaly(dq: DqRunner, manifest: dict[str, Any]) -> None:
    real = [a for a in manifest.values() if a["type"] != "decoy"]
    for r in dq.run_all():
        for f in r.findings:
            assert any(overlaps(f, a) for a in real), f"unexplained finding {f.finding_id}"


def test_binomial_tail() -> None:
    assert binom_sf(0, 10, 0.5) == pytest.approx(1.0)
    assert binom_sf(10, 10, 0.5) == pytest.approx(0.5**10)
    assert binom_sf(4, 12, 0.04) < 1e-3


def test_metric_scanner_finds_cancellation_spike_and_demand_drop(
    full_db: Path, manifest: dict[str, Any]
) -> None:
    scan = MetricScanner(full_db)
    spikes = scan.cancellation_scan()
    assert len(spikes) == 1
    a3 = manifest["A3"]
    assert spikes[0].scope == {"category": "Electronics", "region": "South"}
    assert overlaps(spikes[0], a3)

    drops = scan.demand_scan()
    assert len(drops) == 1
    a4 = manifest["A4"]
    assert drops[0].scope == {"category": "Apparel"} and overlaps(drops[0], a4)


def test_metric_scanner_silent_on_clean_data(tmp_path: Path) -> None:
    from retailops.data.generate import GenConfig, generate
    from retailops.data.load import build_warehouse

    path = tmp_path / "clean.duckdb"
    build_warehouse(generate(GenConfig(orders_per_day=800, inject_anomalies=False)), path)
    scan = MetricScanner(path)
    assert scan.cancellation_scan() == [] and scan.demand_scan() == []
    assert all(r.status == "pass" for r in DqRunner(path).run_all())
