"""Score the deterministic detectors (DQ checks + metric scanners) against the anomaly manifest.

No language model is involved, so this measures the *tools*, not the agent's investigation.
Run `make data` first, then: uv run python -m evals.run_detectors [--write-report]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from evals.evaluators.anomaly import score_detection
from retailops.config import get_settings
from retailops.observability import git_sha
from retailops.tools.dq import DqRunner
from retailops.tools.metrics import MetricScanner

REPORT = Path(__file__).parent / "reports" / "anomaly_detectors.json"
MANIFEST = Path("data/anomaly_manifest.json")


def sweep_seeds(seeds: list[int]) -> list[dict[str, object]]:
    """Regenerate the dataset under other seeds and re-score the SAME detector settings.
    Shows whether the thresholds only work for seed 42."""
    import tempfile

    from retailops.data.generate import GenConfig, generate
    from retailops.data.load import build_warehouse

    rows: list[dict[str, object]] = []
    for seed in seeds:
        data = generate(GenConfig(seed=seed, days=180, orders_per_day=800))
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "w.duckdb"
            build_warehouse(data, db)
            found = [f for r in DqRunner(db).run_all() for f in r.findings]
            scan = MetricScanner(db)
            found += scan.cancellation_scan() + scan.demand_scan()
        sc = score_detection(found, data.manifest["anomalies"])
        rows.append(
            {
                k: sc[k]
                for k in (
                    "recall",
                    "precision",
                    "scope_accuracy",
                    "cause_accuracy",
                    "decoy_false_alarms",
                    "missed_ids",
                    "unexplained_findings",
                )
            }
            | {"seed": seed}
        )
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--write-report", action="store_true")
    p.add_argument("--sweep-seeds", default="", help="comma list, e.g. 1,2,3: regenerate data")
    args = p.parse_args()
    sweep = (
        sweep_seeds([int(x) for x in args.sweep_seeds.split(",") if x]) if args.sweep_seeds else []
    )
    db = get_settings().duckdb_path
    manifest = json.loads(MANIFEST.read_text("utf-8"))

    start = time.perf_counter()
    findings = [f for r in DqRunner(db).run_all() for f in r.findings]
    scan = MetricScanner(db)
    findings += scan.cancellation_scan() + scan.demand_scan()
    elapsed = time.perf_counter() - start

    score = score_detection(findings, manifest["anomalies"])
    report = {
        "what": "deterministic detectors vs ground-truth manifest (no LLM)",
        "git_sha": git_sha(),
        "data_config": {k: manifest[k] for k in ("seed", "days", "orders_per_day")},
        "detector_runtime_s": round(elapsed, 2),
        "score": score,
        "seed_sweep": sweep,
        "findings": [
            {
                "id": f.finding_id,
                "kind": f.kind,
                "start": str(f.start),
                "end": str(f.end),
                "scope": f.scope,
                "count": f.count,
            }
            for f in findings
        ],
    }
    print(
        json.dumps({k: report[k] for k in ("data_config", "detector_runtime_s", "score")}, indent=2)
    )
    for row in sweep:
        print("sweep", row)
    if args.write_report:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
