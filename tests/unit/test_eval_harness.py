"""Evaluation harness tests: scoring, failure classes, collector, report, dataset sync, and both
systems under test (E0, E1) driven by scripted models. No API key or network is used, and no
number produced here is a result about a real model."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from evals import report
from evals.run_eval import Collector, make_scored_target, run_local, sync_dataset
from evals.scoring import classify_failure, score_sql_example
from evals.targets import build_e0, build_e1
from fakes import FakeToolModel, ScriptedLLM
from langchain_core.messages import AIMessage

from retailops.agents.knowledge import KnowledgeDraft
from retailops.agents.sql_analyst import SQLDraft, SQLSummary
from retailops.agents.supervisor import RouteDecision
from retailops.data.generate import GenConfig, generate
from retailops.data.load import build_warehouse
from retailops.rag.chunking import chunk_directory
from retailops.rag.retriever import Retriever, VectorIndex
from retailops.tools.sql import SqlRunner

KNOWLEDGE = Path(__file__).parents[2] / "knowledge"
REF = {"gold_rows": [[10]], "order_matters": False, "question": "How many orders?"}


def out(**kw: Any) -> dict[str, Any]:
    base = {
        "answer": "There were 10 orders.",
        "final_rows": [[10]],
        "sql": "SELECT 10",
        "error": None,
        "intent": None,
        "last_failure_stage": None,
        "sql_attempts": 1,
    }
    return {**base, **kw}


# ---------- scoring ----------


def test_score_correct_and_wrong() -> None:
    assert score_sql_example(out(), REF)["execution_accuracy"] == 1.0
    assert score_sql_example(out(final_rows=[[11]]), REF)["execution_accuracy"] == 0.0
    assert score_sql_example(out(final_rows=None), REF)["valid_sql"] == 0.0
    assert score_sql_example(out(error="boom"), REF)["execution_accuracy"] == 0.0


def test_groundedness_flags_invented_numbers_in_a_correct_result() -> None:
    s = score_sql_example(out(answer="There were 10 orders, 77 of them late."), REF)
    assert s["execution_accuracy"] == 1.0 and s["numeric_groundedness"] == 0.5


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"error": "x"}, "agent_error"),
        ({"intent": "knowledge_question", "final_rows": None}, "misrouted"),
        ({"final_rows": None, "last_failure_stage": "guard"}, "guard_rejected"),
        ({"final_rows": None, "last_failure_stage": "execute"}, "sql_error"),
        ({"final_rows": None}, "no_sql_result"),
        ({"final_rows": []}, "empty_result"),
        ({"final_rows": [[3]]}, "wrong_result"),
    ],
)
def test_failure_classification(kw: dict[str, Any], expected: str) -> None:
    assert classify_failure(out(**kw), correct=False) == expected
    assert classify_failure(out(), correct=True) is None


# ---------- collector, scored target, report ----------


def gold_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "g1",
            "difficulty": "easy",
            "held_out": False,
            "question": "How many orders?",
            "gold_rows": [[10]],
            "gold_columns": ["n"],
            "order_matters": False,
            "data_config": {"seed": 42},
        },
        {
            "id": "g2",
            "difficulty": "hard",
            "held_out": True,
            "question": "Other?",
            "gold_rows": [[5]],
            "gold_columns": ["n"],
            "order_matters": False,
            "data_config": {"seed": 42},
        },
    ]


def fake_system(correct_ids: set[str]) -> Any:
    def system(inputs: dict[str, Any]) -> dict[str, Any]:
        rows = [[10]] if inputs["id"] == "g1" else [[5]]
        if inputs["id"] not in correct_ids:
            rows = [[999]]
        return out(
            final_rows=rows,
            answer="ok",
            latency_s=1.0,
            status="ok",
            tokens={"input": 100, "output": 20},
            tools_used=[],
        )

    return system


def test_collector_assigns_repeat_indices_and_records_failures() -> None:
    c = Collector()
    scored = make_scored_target(fake_system({"g1"}), gold_rows(), c, "E0")
    run_local(scored, gold_rows(), repeats=3)
    assert len(c.records) == 6
    assert sorted({r["repeat"] for r in c.records}) == [0, 1, 2]
    wrong = [r for r in c.records if r["id"] == "g2"]
    assert all(r["failure"] == "wrong_result" and not r["correct"] for r in wrong)


def test_report_aggregates_ranges_and_marks_missing_experiments(tmp_path: Path) -> None:
    c = Collector()
    run_local(make_scored_target(fake_system({"g1"}), gold_rows(), c, "E0"), gold_rows(), 2)
    run = {
        "experiment": "E0",
        "timestamp": "20260101T000000Z",
        "git_sha": "abc",
        "model_ids": {"sql": "m"},
        "prompt_versions": {"p": "v1"},
        "records": c.records,
    }
    (tmp_path / "E0-abc.json").write_text(json.dumps(run))
    runs = report.latest_runs(tmp_path)
    s = report.summarize(runs["E0"])
    assert s["n_questions"] == 2 and s["repeats"] == 2 and s["acc_mean"] == 0.5
    assert s["held_out_acc"] == 0.0 and s["failures"] == [("wrong_result", 2)]
    md = report.render(runs)
    assert "50.0%" in md and "| E1 |" in md
    e1_row = next(line for line in md.splitlines() if line.startswith("| E1 "))
    assert "not yet measured" in e1_row


def test_empty_report_has_no_numbers(tmp_path: Path) -> None:
    md = report.render(report.latest_runs(tmp_path / "missing"))
    assert "%" not in md.split("Interpretation limits")[0].split("|---")[-1].replace(
        "not yet measured", ""
    )


def test_percentile() -> None:
    assert report.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert math.isnan(report.percentile([], 0.5))


class FakeClient:
    def __init__(self, existing_ids: list[str] | None = None) -> None:
        self.created_dataset = False
        self.has = existing_ids is not None
        self.existing = existing_ids or []
        self.added: list[dict[str, Any]] = []

    def has_dataset(self, *, dataset_name: str) -> bool:
        return self.has

    def create_dataset(self, *, dataset_name: str, description: str) -> None:
        self.created_dataset = True

    def list_examples(self, *, dataset_name: str) -> list[Any]:
        return [type("E", (), {"inputs": {"id": i}})() for i in self.existing]

    def create_examples(self, *, dataset_name: str, examples: list[dict[str, Any]]) -> None:
        self.added += examples


def test_dataset_sync_creates_and_only_adds_missing() -> None:
    fresh = FakeClient()
    assert sync_dataset(fresh, "d", gold_rows()) == 2 and fresh.created_dataset
    partial = FakeClient(existing_ids=["g1"])
    assert sync_dataset(partial, "d", gold_rows()) == 1 and not partial.created_dataset
    assert partial.added[0]["inputs"]["id"] == "g2"
    assert "gold_rows" in partial.added[0]["outputs"]
    assert sync_dataset(FakeClient(existing_ids=["g1", "g2"]), "d", gold_rows()) == 0


# ---------- systems under test with scripted models ----------


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory) -> SqlRunner:
    path = tmp_path_factory.mktemp("harness") / "w.duckdb"
    build_warehouse(generate(GenConfig(orders_per_day=20)), path)
    return SqlRunner(path)


@pytest.fixture(scope="module")
def retriever() -> Retriever:
    return Retriever(VectorIndex.build(chunk_directory(KNOWLEDGE)))


def call(name: str, args: dict[str, Any], i: str) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": i, "type": "tool_call"}])


def test_e0_agent_records_tool_use_and_final_rows(runner: SqlRunner, retriever: Retriever) -> None:
    model = FakeToolModel(
        script=[
            call("run_sql", {"query": "SELECT bad FROM fct_orders"}, "1"),
            call("run_sql", {"query": "SELECT count(*) AS n FROM fct_orders"}, "2"),
            AIMessage("Done."),
        ]
    )
    result = build_e0(model, runner, retriever)({"id": "g1", "question": "How many orders?"})
    assert result["tools_used"] == ["run_sql", "run_sql"] and result["sql_attempts"] == 2
    assert result["final_rows"] and result["final_rows"][0][0] > 0 and result["error"] is None
    assert result["answer"] == "Done." and result["status"] == "ok"


def test_e0_reports_guard_failure_stage(runner: SqlRunner, retriever: Retriever) -> None:
    model = FakeToolModel(
        script=[call("run_sql", {"query": "DROP TABLE fct_orders"}, "1"), AIMessage("Can't.")]
    )
    result = build_e0(model, runner, retriever)({"id": "g1", "question": "Drop it"})
    assert result["final_rows"] is None and result["last_failure_stage"] == "guard"
    assert result["status"] == "no_sql_result"


def test_e0_agent_errors_are_recorded_not_raised(runner: SqlRunner, retriever: Retriever) -> None:
    model = FakeToolModel(script=[])  # exhausted immediately -> AssertionError inside the agent
    result = build_e0(model, runner, retriever)({"id": "g1", "question": "q"})
    assert result["status"] == "error" and result["error"]


def test_e1_target_returns_the_same_shape(runner: SqlRunner, retriever: Retriever) -> None:
    sup = ScriptedLLM(
        {
            RouteDecision: [
                RouteDecision(
                    intent="metric_question", standalone_question="How many orders?", plan=["count"]
                )
            ]
        }
    )
    sql = ScriptedLLM(
        {
            SQLDraft: [SQLDraft(sql="SELECT count(*) AS n FROM fct_orders")],
            SQLSummary: [SQLSummary(answer="ok", confidence_note="direct")],
        }
    )
    kn = ScriptedLLM({KnowledgeDraft: []})
    result = build_e1(sup, sql, kn, runner, retriever)({"id": "g1", "question": "How many orders?"})
    assert result["intent"] == "metric_question" and result["final_rows"][0][0] > 0
    assert result["sql_attempts"] == 1 and result["error"] is None
    assert set(result) >= set(out())
