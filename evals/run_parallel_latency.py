"""E3 (offline part): wall time of the investigation workers, parallel Send fan-out vs the same
workers run one after another. Language-model calls are replaced by instant scripted responses,
so this measures ONLY the tool/worker part of an investigation, not end-to-end latency.

    uv run python -m evals.run_parallel_latency [--repeats 10] [--write-report]
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from retailops.agents.investigation import (
    InvestigationScope,
    InvestigationSummary,
    build_investigation,
    investigate,
)
from retailops.config import get_settings
from retailops.observability import git_sha
from retailops.tools.dq import DqRunner
from retailops.tools.metrics import MetricScanner

REPORT = Path(__file__).parent / "reports" / "parallel_latency.json"


class _Instant:
    """Scripted model: returns the same structured outputs immediately, with no network."""

    def with_structured_output(self, schema: Any) -> Any:
        class Bound:
            def invoke(self, messages: Any, config: Any = None) -> Any:
                if schema is InvestigationScope:
                    return InvestigationScope(focus="all")
                return InvestigationSummary(answer="n/a", confidence_note="n/a")

        return Bound()


def percentile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def measure(db: str, parallel: bool, repeats: int) -> list[float]:
    graph = build_investigation(_Instant(), DqRunner(db), MetricScanner(db), parallel=parallel)
    investigate(graph, "warm-up")  # exclude first-call import/connection costs
    return [investigate(graph, "q").wall_time_s for _ in range(repeats)]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repeats", type=int, default=10)
    p.add_argument("--write-report", action="store_true")
    args = p.parse_args()
    db = get_settings().duckdb_path
    # interleave to reduce drift: sequential first, then parallel, then again
    seq = measure(db, False, args.repeats)
    par = measure(db, True, args.repeats)
    out = {
        "what": "investigation worker wall time, LLM calls scripted (excluded)",
        "git_sha": git_sha(),
        "repeats": args.repeats,
        "sequential_s": {
            "p50": round(statistics.median(seq), 3),
            "p95": round(percentile(seq, 0.95), 3),
        },
        "parallel_s": {
            "p50": round(statistics.median(par), 3),
            "p95": round(percentile(par, 0.95), 3),
        },
        "speedup_p50": round(statistics.median(seq) / statistics.median(par), 2),
    }
    print(json.dumps(out, indent=2))
    if args.write_report:
        REPORT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
