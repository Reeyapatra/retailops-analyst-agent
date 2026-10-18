from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from evals.evaluators.anomaly import score_detection

from retailops.tools.dq import DqRunner, Finding, classify_cause
from retailops.tools.metrics import MetricScanner


def f(kind: str, start: str, end: str, **scope: str) -> Finding:
    return Finding(
        finding_id=f"{kind}:{start}",
        kind=kind,  # type: ignore[arg-type]
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        scope=scope,
        count=1,
    )


def manifest() -> list[dict[str, Any]]:
    return [
        {
            "anomaly_id": "A1",
            "type": "late_arriving_events",
            "start": "2026-03-01",
            "end": "2026-03-02",
            "scope": {"region": "West"},
        },
        {
            "anomaly_id": "A2",
            "type": "revenue_drop_real",
            "start": "2026-04-01",
            "end": "2026-04-14",
            "scope": {"category": "Apparel"},
        },
        {
            "anomaly_id": "D1",
            "type": "decoy",
            "start": "2026-05-01",
            "end": "2026-05-07",
            "scope": {},
        },
    ]


def test_perfect_detection() -> None:
    found = [
        f("late_events", "2026-03-01", "2026-03-02", region="West"),
        f("demand_drop", "2026-04-02", "2026-04-16", category="Apparel"),
    ]
    s = score_detection(found, manifest())
    assert (s["recall"], s["precision"], s["scope_accuracy"], s["cause_accuracy"]) == (1, 1, 1, 1)
    assert s["decoy_false_alarms"] == 0 and s["unexplained_findings"] == []


def test_wrong_scope_wrong_kind_and_misses_are_counted() -> None:
    found = [
        f("late_events", "2026-03-01", "2026-03-02", region="East"),  # wrong scope
        f("duplicate_events", "2026-04-02", "2026-04-03"),
    ]  # wrong kind for A2
    s = score_detection(found, manifest())
    assert s["recall"] == 0.5 and s["scope_accuracy"] == 0.0 and s["detected_ids"] == ["A1"]
    assert s["missed_ids"] == ["A2"] and s["precision"] == 0.5


def test_decoy_hits_are_false_alarms() -> None:
    found = [f("late_events", "2026-05-03", "2026-05-04", region="West")]
    s = score_detection(found, manifest())
    assert s["decoy_false_alarms"] == 1 and s["decoy_false_alarm_rate"] == 1.0
    assert s["precision"] == 0.0


def test_cause_classification_distinguishes_data_issue_from_business_change() -> None:
    drop = f("demand_drop", "2026-04-02", "2026-04-16", category="Apparel")
    assert classify_cause(drop, []) == "business_change"
    overlapping_dq = f("duplicate_events", "2026-04-10", "2026-04-10")
    assert classify_cause(drop, [overlapping_dq]) == "data_quality"
    assert classify_cause(overlapping_dq, []) == "data_quality"
    elsewhere = f("duplicate_events", "2026-06-10", "2026-06-10")
    assert classify_cause(drop, [elsewhere]) == "business_change"


def test_detectors_on_the_full_dataset_score_against_the_manifest(
    full_db: Path, full_data: Any
) -> None:
    """Regression guard for the offline detector result reported in evals/reports."""
    findings = [x for r in DqRunner(full_db).run_all() for x in r.findings]
    scan = MetricScanner(full_db)
    findings += scan.cancellation_scan() + scan.demand_scan()
    s = score_detection(findings, full_data.manifest["anomalies"])
    assert s["recall"] == 1.0 and s["decoy_false_alarms"] == 0 and s["precision"] == 1.0
    assert s["cause_accuracy"] == 1.0
