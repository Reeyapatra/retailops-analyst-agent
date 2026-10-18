"""Score detected findings against the ground-truth anomaly manifest (docs/04 section 3).

Detection: a finding of an acceptable kind whose date window overlaps the anomaly window.
Scope accuracy: among detected anomalies, the matching finding carries every manifest scope key
with the right value. Cause accuracy: the finding's data-quality-vs-business classification
equals the manifest's. Decoy windows must produce no findings at all.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from retailops.tools.dq import DQ_KINDS, Finding, classify_cause

KINDS_FOR_TYPE: dict[str, set[str]] = {
    "duplicate_events": {"duplicate_events"},
    "late_arriving_events": {"late_events"},
    "cancellation_spike": {"cancellation_spike"},
    "revenue_drop_real": {"demand_drop"},
    "invalid_payments": {"invalid_payments"},
    "missing_shipments": {"missing_shipments"},
    "schema_drift": {"schema_drift", "null_fields"},
}
# Expected classification of each injected anomaly: pipeline defect vs real business change.
CAUSE_FOR_TYPE: dict[str, str] = {
    t: "data_quality" if t not in {"cancellation_spike", "revenue_drop_real"} else "business_change"
    for t in KINDS_FOR_TYPE
}


def _overlaps(f: Finding, a: dict[str, Any]) -> bool:
    return f.start <= date.fromisoformat(a["end"]) and date.fromisoformat(a["start"]) <= f.end


def _scope_ok(f: Finding, a: dict[str, Any]) -> bool:
    return all(f.scope.get(k) == v for k, v in a["scope"].items())


def score_detection(findings: list[Finding], anomalies: list[dict[str, Any]]) -> dict[str, Any]:
    real = [a for a in anomalies if a["type"] != "decoy"]
    decoys = [a for a in anomalies if a["type"] == "decoy"]
    dq_findings = [f for f in findings if f.kind in DQ_KINDS]

    detected: list[str] = []
    scope_ok: list[str] = []
    cause_ok: list[str] = []
    explained: set[str] = set()
    for a in real:
        candidates = [
            f for f in findings if f.kind in KINDS_FOR_TYPE[a["type"]] and _overlaps(f, a)
        ]
        explained.update(f.finding_id for f in candidates)
        if not candidates:
            continue
        detected.append(a["anomaly_id"])
        best = next((f for f in candidates if _scope_ok(f, a)), None)
        if best is not None:
            scope_ok.append(a["anomaly_id"])
        probe = best or candidates[0]
        if classify_cause(probe, dq_findings) == CAUSE_FOR_TYPE[a["type"]]:
            cause_ok.append(a["anomaly_id"])

    decoy_hits = [d["anomaly_id"] for d in decoys if any(_overlaps(f, d) for f in findings)]
    return {
        "n_anomalies": len(real),
        "n_decoys": len(decoys),
        "n_findings": len(findings),
        "recall": len(detected) / len(real) if real else 0.0,
        "scope_accuracy": len(scope_ok) / len(detected) if detected else 0.0,
        "cause_accuracy": len(cause_ok) / len(detected) if detected else 0.0,
        "precision": len(explained) / len(findings) if findings else 1.0,
        "decoy_false_alarms": len(decoy_hits),
        "decoy_false_alarm_rate": len(decoy_hits) / len(decoys) if decoys else 0.0,
        "detected_ids": detected,
        "missed_ids": [a["anomaly_id"] for a in real if a["anomaly_id"] not in detected],
        "scope_wrong_ids": [i for i in detected if i not in scope_ok],
        "cause_wrong_ids": [i for i in detected if i not in cause_ok],
        "unexplained_findings": [f.finding_id for f in findings if f.finding_id not in explained],
        "decoy_hit_ids": decoy_hits,
    }
