"""Structure checks for the smaller eval datasets."""

from __future__ import annotations

import json
from pathlib import Path

from retailops.rag.chunking import chunk_directory

ROOT = Path(__file__).parents[2]


def rows(name: str) -> list[dict]:  # type: ignore[type-arg]
    return [
        json.loads(x) for x in (ROOT / "evals/datasets" / name).read_text("utf-8").splitlines() if x
    ]


def test_rag_gold_references_real_documents() -> None:
    docs = {c.doc_id for c in chunk_directory(ROOT / "knowledge")}
    gold = rows("rag_gold.jsonl")
    assert len(gold) >= 30 and len({g["id"] for g in gold}) == len(gold)
    assert sum(not g["answerable"] for g in gold) >= 6
    for g in gold:
        assert set(g["gold_doc_ids"]) <= docs, g["id"]
        assert g["answerable"] == bool(g["gold_doc_ids"])


def test_routing_smoke_covers_all_intents_and_follow_ups() -> None:
    cases = rows("routing_smoke.jsonl")
    assert len(cases) >= 15
    assert {c["expected_intent"] for c in cases} == {
        "metric_question",
        "data_quality",
        "anomaly_investigation",
        "knowledge_question",
        "out_of_scope",
    }
    assert any(len(c["turns"]) > 1 for c in cases)
