"""Measure retrieval hit@k, MRR and abstention accuracy on rag_gold (no model or API key needed).

    uv run python -m evals.run_rag_retrieval [--min-score 0.12] [--k 5] [--write-report]

Threshold selection must use only the dev split; held-out numbers are for reporting.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from retailops.rag.chunking import chunk_directory
from retailops.rag.retriever import DEFAULT_K, DEFAULT_MIN_SCORE, Retriever, VectorIndex

GOLD = Path(__file__).parent / "datasets" / "rag_gold.jsonl"
KNOWLEDGE = Path(__file__).parents[1] / "knowledge"
REPORTS = Path(__file__).parent / "reports"


def load_gold() -> list[dict[str, Any]]:
    return [json.loads(x) for x in GOLD.read_text("utf-8").splitlines() if x]


def evaluate(retriever: Retriever, gold: list[dict[str, Any]], k: int) -> dict[str, float | int]:
    """hit@k and MRR are computed on answerable questions from the *unthresholded* top-k;
    abstention accuracy uses the thresholded decision on all questions."""
    answerable = [g for g in gold if g["answerable"]]
    unanswerable = [g for g in gold if not g["answerable"]]
    hits = 0
    rr_total = 0.0
    answered_ok = 0
    for g in answerable:
        res = retriever.retrieve(g["question"], k=k)
        ranked = [r.chunk.doc_id for r in _unthresholded(retriever, g["question"], k)]
        rank = next((i + 1 for i, d in enumerate(ranked) if d in g["gold_doc_ids"]), None)
        if rank:
            hits += 1
            rr_total += 1 / rank
        if not res.abstain:
            answered_ok += 1
    abstained_ok = sum(retriever.retrieve(g["question"], k=k).abstain for g in unanswerable)
    return {
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        f"hit@{k}": round(hits / len(answerable), 3) if answerable else 0.0,
        "mrr": round(rr_total / len(answerable), 3) if answerable else 0.0,
        "answered_when_answerable": round(answered_ok / len(answerable), 3) if answerable else 0.0,
        "abstained_when_unanswerable": (
            round(abstained_ok / len(unanswerable), 3) if unanswerable else 0.0
        ),
    }


def _unthresholded(retriever: Retriever, query: str, k: int) -> list[Any]:
    lenient = Retriever(retriever.index, k=k, min_score=-1.0)
    return lenient.retrieve(query, k=k).hits


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    p.add_argument("--k", type=int, default=DEFAULT_K)
    p.add_argument("--write-report", action="store_true")
    args = p.parse_args()

    index = VectorIndex.build(chunk_directory(KNOWLEDGE))
    retriever = Retriever(index, k=args.k, min_score=args.min_score)
    gold = load_gold()
    report = {
        "embedder": index.embedder.name,
        "min_score": args.min_score,
        "k": args.k,
        "n_chunks": len(index.chunks),
        "all": evaluate(retriever, gold, args.k),
        "dev": evaluate(retriever, [g for g in gold if not g["held_out"]], args.k),
        "held_out": evaluate(retriever, [g for g in gold if g["held_out"]], args.k),
    }
    print(json.dumps(report, indent=2))
    if args.write_report:
        REPORTS.mkdir(parents=True, exist_ok=True)
        path = REPORTS / "rag_retrieval.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
