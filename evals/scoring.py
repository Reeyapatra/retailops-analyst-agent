"""Per-example scoring and failure classification shared by every experiment."""

from __future__ import annotations

from typing import Any

from evals.evaluators.execution_accuracy import results_match
from evals.evaluators.groundedness import citation_validity, numeric_groundedness

FAILURE_CATEGORIES = (
    "agent_error",
    "misrouted",
    "guard_rejected",
    "sql_error",
    "empty_result",
    "no_sql_result",
    "wrong_result",
)


def score_sql_example(outputs: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    """Scores for one run of a system on one sql_gold example."""
    rows = outputs.get("final_rows")
    correct = (
        outputs.get("error") is None
        and rows is not None
        and results_match(reference["gold_rows"], rows, order_matters=reference["order_matters"])
    )
    ground = numeric_groundedness(
        outputs.get("answer", ""),
        rows=rows or [],
        question=reference.get("question", ""),
        sql=outputs.get("sql") or "",
    )
    scores: dict[str, Any] = {
        "execution_accuracy": 1.0 if correct else 0.0,
        "numeric_groundedness": ground["score"],
        "answer_has_numbers": ground["n_numbers"] > 0,
        "valid_sql": 1.0 if outputs.get("final_rows") is not None else 0.0,
        "sql_attempts": outputs.get("sql_attempts"),
    }
    if outputs.get("retrieved_ids") is not None or outputs.get("cited_ids"):
        scores["citation_validity"] = citation_validity(
            cited=outputs.get("cited_ids", []), retrieved=outputs.get("retrieved_ids", [])
        )["score"]
    return scores


def classify_failure(outputs: dict[str, Any], correct: bool) -> str | None:
    """One category per incorrect run (None when correct)."""
    if correct:
        return None
    if outputs.get("error"):
        return "agent_error"
    intent = outputs.get("intent")
    if intent is not None and intent not in {"metric_question", "data_quality"}:
        return "misrouted"
    stage = outputs.get("last_failure_stage")
    if outputs.get("final_rows") is None:
        if stage == "guard":
            return "guard_rejected"
        if stage == "execute":
            return "sql_error"
        return "no_sql_result"
    if not outputs["final_rows"]:
        return "empty_result"
    return "wrong_result"
