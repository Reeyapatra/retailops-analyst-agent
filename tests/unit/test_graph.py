"""Supervisor routing, specialist dispatch and thread memory, with scripted models.

These verify orchestration. Whether a real model routes correctly is measured separately by
evals/run_routing_smoke.py, which needs an API key and has not been run yet.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fakes import ScriptedLLM
from langchain_core.exceptions import OutputParserException
from langgraph.checkpoint.sqlite import SqliteSaver

from retailops.agents.knowledge import KnowledgeDraft
from retailops.agents.sql_analyst import SQLDraft, SQLSummary
from retailops.agents.supervisor import RouteDecision, route
from retailops.data.generate import GenConfig, generate
from retailops.data.load import build_warehouse
from retailops.graph import DECLINE, MAX_INPUT_CHARS, UNAVAILABLE, build_app, chat
from retailops.rag.chunking import chunk_directory
from retailops.rag.retriever import Retriever, VectorIndex
from retailops.tools.sql import SqlRunner

KNOWLEDGE = Path(__file__).parents[2] / "knowledge"


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory) -> SqlRunner:
    path = tmp_path_factory.mktemp("graph") / "w.duckdb"
    build_warehouse(generate(GenConfig(orders_per_day=20)), path)
    return SqlRunner(path)


@pytest.fixture(scope="module")
def retriever() -> Retriever:
    return Retriever(VectorIndex.build(chunk_directory(KNOWLEDGE)))


def decision(intent: str, q: str = "q") -> RouteDecision:
    return RouteDecision(intent=intent, standalone_question=q, plan=["step"])  # type: ignore[arg-type]


def make_app(
    runner: SqlRunner,
    retriever: Retriever,
    routes: list[Any],
    drafts: list[str] | None = None,
    knowledge: list[KnowledgeDraft] | None = None,
    checkpointer: Any = None,
) -> tuple[Any, ScriptedLLM, ScriptedLLM, ScriptedLLM]:
    sup = ScriptedLLM({RouteDecision: routes})
    sql = ScriptedLLM(
        {
            SQLDraft: [SQLDraft(sql=s) for s in drafts or []],
            SQLSummary: [SQLSummary(answer="done", confidence_note="direct")] * len(drafts or []),
        }
    )
    kn = ScriptedLLM({KnowledgeDraft: knowledge or []})
    app = build_app(
        supervisor_llm=sup,
        sql_llm=sql,
        knowledge_llm=kn,
        runner=runner,
        retriever=retriever,
        checkpointer=checkpointer,
    )
    return app, sup, sql, kn


def test_metric_question_goes_to_sql_analyst(runner: SqlRunner, retriever: Retriever) -> None:
    app, _, sql, kn = make_app(
        runner, retriever, [decision("metric_question")], ["SELECT count(*) AS n FROM fct_orders"]
    )
    r = chat(app, "How many orders?", "t-metric")
    assert r.intent == "metric_question" and r.status == "ok" and r.answer == "done"
    assert r.evidence and r.evidence[0]["row_count"] == 1
    assert kn.calls == []


def test_knowledge_question_goes_to_knowledge_with_citations(
    runner: SqlRunner, retriever: Retriever
) -> None:
    kd = KnowledgeDraft(
        answerable=True,
        answer="Net of cancellations.",
        citations=["metric_definitions#revenue"],
    )
    app, _, sql, _ = make_app(
        runner,
        retriever,
        [decision("knowledge_question", "How is net revenue defined?")],
        knowledge=[kd],
    )
    r = chat(app, "How is net revenue defined?", "t-know")
    assert r.status == "answered" and r.citations[0]["doc_id"] == "metric_definitions"
    assert sql.calls == []


def test_unknown_topic_abstains_without_calling_the_knowledge_model(
    runner: SqlRunner, retriever: Retriever
) -> None:
    app, _, _, kn = make_app(
        runner,
        retriever,
        [decision("knowledge_question", "How do I configure Kubernetes autoscaling?")],
    )
    r = chat(app, "How do I configure Kubernetes autoscaling?", "t-abstain")
    assert r.status == "abstained" and r.citations == [] and kn.calls == []


def test_out_of_scope_and_unavailable_are_explicit(runner: SqlRunner, retriever: Retriever) -> None:
    app, _, sql, kn = make_app(
        runner, retriever, [decision("out_of_scope"), decision("anomaly_investigation")]
    )
    a = chat(app, "Drop the orders table", "t-oos")
    b = chat(app, "Why did revenue fall in March?", "t-anom")
    assert (a.status, a.answer) == ("declined", DECLINE)
    assert (b.status, b.answer) == ("unsupported", UNAVAILABLE)
    assert sql.calls == [] and kn.calls == []


def test_bad_input_is_rejected_before_routing(runner: SqlRunner, retriever: Retriever) -> None:
    app, sup, _, _ = make_app(runner, retriever, [])
    assert chat(app, "   ", "t-empty").status == "rejected"
    assert chat(app, "x" * (MAX_INPUT_CHARS + 1), "t-long").status == "rejected"
    assert sup.calls == []


def test_supervisor_falls_back_safely_after_two_invalid_outputs() -> None:
    class Flaky:
        calls = 0

        def with_structured_output(self, schema: Any) -> Flaky:
            return self

        def invoke(self, messages: Any, config: Any = None) -> Any:
            Flaky.calls += 1
            raise OutputParserException("bad json")

    decision_, fallback = route(Flaky(), "something odd")
    assert fallback and decision_.intent == "knowledge_question"
    assert decision_.standalone_question == "something odd" and Flaky.calls == 2


def test_provider_errors_are_not_swallowed() -> None:
    class Down:
        def with_structured_output(self, schema: Any) -> Down:
            return self

        def invoke(self, messages: Any, config: Any = None) -> Any:
            raise ConnectionError("network down")

    with pytest.raises(ConnectionError):
        route(Down(), "hello")


def test_thread_memory_supports_follow_ups(
    runner: SqlRunner, retriever: Retriever, tmp_path: Path
) -> None:
    conn = sqlite3.connect(tmp_path / "ck.sqlite", check_same_thread=False)
    saver = SqliteSaver(conn)
    app, sup, sql, _ = make_app(
        runner,
        retriever,
        [
            decision("metric_question", "Revenue by region in March 2026"),
            decision("metric_question", "Revenue in the West region in March 2026"),
            decision("metric_question", "Revenue by region"),
        ],
        [
            "SELECT region, sum(net_revenue) FROM fct_orders GROUP BY 1",
            "SELECT sum(net_revenue) FROM fct_orders WHERE region = 'West'",
            "SELECT region FROM dim_regions",
        ],
        checkpointer=saver,
    )
    chat(app, "Revenue by region in March 2026", "t-mem")
    second = chat(app, "and for the West region?", "t-mem")

    first_prompt = sup.calls[0][1][1].content
    second_prompt = sup.calls[1][1][1].content
    assert "Conversation so far" not in first_prompt
    assert "Revenue by region in March 2026" in second_prompt  # history reached the supervisor
    assert "and for the West region?" in second_prompt
    sql_prompt = sql.calls[2][1][1].content  # second SQLDraft call
    assert "Revenue in the West region in March 2026" in sql_prompt  # standalone question used
    assert second.status == "ok"

    # per-turn findings do not accumulate across turns on the same thread
    state = app.get_state({"configurable": {"thread_id": "t-mem"}}).values
    assert len(state["findings"]) == 1 and len(state["messages"]) == 4

    # a different thread starts with no history
    chat(app, "Revenue by region", "t-other")
    assert "Conversation so far" not in sup.calls[2][1][1].content
    conn.close()
