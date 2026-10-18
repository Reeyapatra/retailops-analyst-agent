"""Experiment runner: score a system (E0 baseline or E1 multi-agent) on sql_gold.

    uv run python -m evals.run_eval --experiment E0 --repeats 3
    uv run python -m evals.run_eval --experiment E1 --repeats 3 --langsmith

Needs MODEL_* and the provider key (and LANGSMITH_API_KEY for --langsmith). Each invocation
writes evals/reports/runs/<experiment>-<git_sha>-<timestamp>.json; the results table is built
from those files by `python -m evals.report`. Nothing is estimated: no file means no number.
"""

from __future__ import annotations

import argparse
import json
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.scoring import classify_failure, score_sql_example
from evals.targets import Target, build_e0, build_e1
from retailops.config import get_settings
from retailops.models import get_chat_model
from retailops.observability import git_sha
from retailops.prompts import PROMPT_VERSIONS
from retailops.rag.retriever import Retriever
from retailops.tools.sql import SqlRunner

ROOT = Path(__file__).parent
GOLD = ROOT / "datasets" / "sql_gold.jsonl"
RUNS_DIR = ROOT / "reports" / "runs"
DATASET_NAME = "retailops-sql_gold"


def load_gold(path: Path = GOLD) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x]


class Collector:
    """Thread-safe record store; repeat index = how many times an example was already run."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self._counts: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def next_repeat(self, example_id: str) -> int:
        with self._lock:
            n = self._counts[example_id]
            self._counts[example_id] += 1
            return n

    def add(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.records.append(record)


def make_scored_target(
    system: Target, gold: list[dict[str, Any]], collector: Collector, experiment: str
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    by_id = {g["id"]: g for g in gold}

    def scored(inputs: dict[str, Any]) -> dict[str, Any]:
        ref = by_id[inputs["id"]]
        repeat = collector.next_repeat(ref["id"])
        out = system({"id": ref["id"], "question": ref["question"]})
        scores = score_sql_example(out, ref)
        correct = scores["execution_accuracy"] == 1.0
        collector.add(
            {
                "experiment": experiment,
                "id": ref["id"],
                "repeat": repeat,
                "difficulty": ref["difficulty"],
                "held_out": ref["held_out"],
                "correct": correct,
                "scores": scores,
                "failure": classify_failure(out, correct),
                "status": out["status"],
                "intent": out.get("intent"),
                "latency_s": out["latency_s"],
                "tokens": out["tokens"],
                "tools_used": out.get("tools_used", []),
                "sql_attempts": out.get("sql_attempts"),
                "error": out.get("error"),
                "answer": (out.get("answer") or "")[:500],
                "sql": out.get("sql"),
            }
        )
        return {**out, "scores": scores}

    return scored


def run_local(
    scored: Callable[[dict[str, Any]], dict[str, Any]], gold: list[dict[str, Any]], repeats: int
) -> None:
    for _ in range(repeats):
        for g in gold:
            scored({"id": g["id"], "question": g["question"]})


def sync_dataset(client: Any, name: str, gold: list[dict[str, Any]]) -> int:
    """Create the LangSmith dataset if needed and add any examples not already present.
    Existing examples are not updated; local JSONL stays the source of truth."""
    if not client.has_dataset(dataset_name=name):
        client.create_dataset(
            dataset_name=name, description="RetailOps sql_gold (synced from evals/datasets)"
        )
    existing = {e.inputs.get("id") for e in client.list_examples(dataset_name=name)}
    new = [
        {
            "inputs": {"id": g["id"], "question": g["question"]},
            "outputs": {
                "gold_rows": g["gold_rows"],
                "gold_columns": g["gold_columns"],
                "order_matters": g["order_matters"],
                "difficulty": g["difficulty"],
                "held_out": g["held_out"],
            },
        }
        for g in gold
        if g["id"] not in existing
    ]
    if new:
        client.create_examples(dataset_name=name, examples=new)
    return len(new)


def langsmith_evaluators() -> list[Callable[..., dict[str, Any]]]:
    def make(key: str) -> Callable[..., dict[str, Any]]:
        def evaluator(
            inputs: dict[str, Any], outputs: dict[str, Any], reference_outputs: dict[str, Any]
        ) -> dict[str, Any]:
            return {"key": key, "score": outputs["scores"][key]}

        evaluator.__name__ = key
        return evaluator

    return [make("execution_accuracy"), make("numeric_groundedness"), make("valid_sql")]


def build_system(experiment: str, runner: SqlRunner, retriever: Retriever) -> Target:
    if experiment == "E0":
        return build_e0(get_chat_model("sql"), runner, retriever)
    if experiment == "E1":
        return build_e1(
            get_chat_model("supervisor"),
            get_chat_model("sql"),
            get_chat_model("sql"),
            runner,
            retriever,
        )
    raise SystemExit(f"unknown experiment {experiment!r} (M4 supports E0 and E1)")


def write_run(experiment: str, repeats: int, collector: Collector, meta: dict[str, Any]) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RUNS_DIR / f"{experiment}-{meta['git_sha']}-{stamp}.json"
    payload = {
        "experiment": experiment,
        "repeats": repeats,
        "timestamp": stamp,
        **meta,
        "records": sorted(collector.records, key=lambda r: (r["repeat"], r["id"])),
    }
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--experiment", choices=["E0", "E1"], required=True)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--limit", type=int, default=None, help="first N questions (smoke test)")
    p.add_argument("--langsmith", action="store_true")
    p.add_argument("--concurrency", type=int, default=2)
    args = p.parse_args()

    s = get_settings()
    from retailops.rag.chunking import chunk_directory
    from retailops.rag.retriever import VectorIndex

    retriever = Retriever(VectorIndex.build(chunk_directory(ROOT.parent / "knowledge")))
    gold = load_gold()[: args.limit]
    system = build_system(args.experiment, SqlRunner(s.duckdb_path), retriever)
    collector = Collector()
    scored = make_scored_target(system, gold, collector, args.experiment)
    sha = git_sha()
    meta = {
        "git_sha": sha,
        "dataset": "sql_gold",
        "n_questions": len(gold),
        "model_ids": {"supervisor": s.model_supervisor, "sql": s.model_sql},
        "prompt_versions": dict(PROMPT_VERSIONS),
        "data_config": gold[0]["data_config"],
        "langsmith": args.langsmith,
    }

    if args.langsmith:
        from langsmith import Client, evaluate

        client = Client()
        added = sync_dataset(client, DATASET_NAME, load_gold())
        print(f"dataset {DATASET_NAME}: {added} new examples synced")
        evaluate(
            scored,
            data=DATASET_NAME,
            evaluators=langsmith_evaluators(),
            experiment_prefix=f"retailops-{args.experiment}-{sha}",
            num_repetitions=args.repeats,
            max_concurrency=args.concurrency,
            metadata={k: v for k, v in meta.items() if k != "langsmith"},
        )
    else:
        run_local(scored, gold, args.repeats)

    path = write_run(args.experiment, args.repeats, collector, meta)
    n = len(collector.records)
    acc = sum(r["correct"] for r in collector.records) / n if n else 0.0
    print(f"{args.experiment}: {n} runs, execution accuracy {acc:.1%} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
