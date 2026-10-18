"""Run the SQL Analyst on the gold questions with a REAL model and report execution accuracy.

Requires MODEL_SQL and the provider key (e.g. ANTHROPIC_API_KEY) in .env, and `make data`.
Set LANGSMITH_TRACING=true and LANGSMITH_API_KEY to trace each question.
This is the M2 spot check; the full experiment runner with LangSmith datasets is M4.

    uv run python -m evals.run_sql_gold [--limit N] [--only g01,g02]

Nothing here has been run without a key: any accuracy figure must come from an actual run.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from evals.evaluators.execution_accuracy import results_match
from retailops.agents.sql_analyst import ask_sql, build_sql_analyst
from retailops.config import get_settings
from retailops.models import get_chat_model
from retailops.tools.sql import SqlRunner

GOLD = Path(__file__).parent / "datasets" / "sql_gold.jsonl"
REPORTS = Path(__file__).parent / "reports"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--only", default="")
    args = p.parse_args()

    gold: list[dict[str, Any]] = [json.loads(x) for x in GOLD.read_text("utf-8").splitlines() if x]
    if args.only:
        wanted = set(args.only.split(","))
        gold = [g for g in gold if g["id"] in wanted]
    if args.limit:
        gold = gold[: args.limit]

    settings = get_settings()
    graph = build_sql_analyst(get_chat_model("sql"), SqlRunner(settings.duckdb_path))

    records: list[dict[str, Any]] = []
    for g in gold:
        start = time.perf_counter()
        out = ask_sql(graph, g["question"], scenario_id=f"sql_gold:{g['id']}")
        elapsed = time.perf_counter() - start
        correct = (
            out.evidence is not None
            and out.status == "ok"
            and results_match(g["gold_rows"], out.evidence.rows, order_matters=g["order_matters"])
        )
        records.append(
            {
                "id": g["id"],
                "difficulty": g["difficulty"],
                "held_out": g["held_out"],
                "correct": correct,
                "status": out.status,
                "attempts": len(out.attempts),
                "latency_s": round(elapsed, 2),
                "sql": out.evidence.sql if out.evidence else None,
            }
        )
        print(f"{g['id']} {g['difficulty']:6} {'OK ' if correct else 'BAD'} {out.status}")

    n = len(records)
    acc = sum(r["correct"] for r in records) / n if n else 0.0
    print(f"\nexecution accuracy: {sum(r['correct'] for r in records)}/{n} = {acc:.1%}")
    if n:
        print(f"latency p50: {statistics.median(r['latency_s'] for r in records):.1f}s")

    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = REPORTS / f"sql_gold_spotcheck_{stamp}.json"
    path.write_text(
        json.dumps(
            {"model": settings.model_sql, "n": n, "accuracy": acc, "records": records}, indent=2
        ),
        encoding="utf-8",
    )
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
