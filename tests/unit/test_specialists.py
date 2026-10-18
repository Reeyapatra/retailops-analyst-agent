"""Data Quality agent, parallel investigation, handoff and agents-as-tools, with scripted models.

Findings come from real SQL over the generated warehouse; only the language steps (planning,
narration) are scripted, so these tests prove orchestration and tool use, not model quality."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fakes import ScriptedLLM

from retailops.agents.data_quality import CheckPlan, DQSummary, PlannedCheck, run_data_quality
from retailops.agents.investigation import (
    InvestigationScope,
    InvestigationSummary,
    build_investigation,
    investigate,
)
from retailops.agents.knowledge import KnowledgeDraft
from retailops.agents.sql_analyst import SQLDraft, SQLSummary, ask_sql, build_sql_analyst
from retailops.agents.supervisor import RouteDecision
from retailops.graph import build_app, chat
from retailops.rag.chunking import chunk_directory
from retailops.rag.retriever import Retriever, VectorIndex
from retailops.tools.calendar import CalendarClient
from retailops.tools.dq import DqRunner
from retailops.tools.knowledge_tool import KnowledgeBudgetExceeded, make_ask_knowledge_tool
from retailops.tools.metrics import MetricScanner
from retailops.tools.signals import corruption_signals
from retailops.tools.sql import SqlRunner

KNOWLEDGE = Path(__file__).parents[2] / "knowledge"
SUMMARY = DQSummary(answer="summary", confidence_note="ok")


@pytest.fixture(scope="module")
def retriever() -> Retriever:
    return Retriever(VectorIndex.build(chunk_directory(KNOWLEDGE)))


@pytest.fixture(scope="module")
def runner(full_db: Path) -> SqlRunner:
    return SqlRunner(full_db)


# ---------- data quality agent ----------


def test_dq_agent_runs_only_validated_planned_checks(full_db: Path) -> None:
    llm = ScriptedLLM(
        {
            CheckPlan: [
                CheckPlan(
                    checks=[
                        PlannedCheck(name="invalid_payments"),
                        PlannedCheck(name="late_events", params={"region": "Mars"}),  # invalid
                    ]
                )
            ],
            DQSummary: [SUMMARY],
        }
    )
    out = run_data_quality(llm, DqRunner(full_db), "Are there bad payments?")
    assert out.checks_run == ["invalid_payments"] and not out.plan_fallback
    assert [f.kind for f in out.findings] == ["invalid_payments"]
    summary_prompt = llm.calls[-1][1][1].content
    assert "<untrusted_data" in summary_prompt and "rejected" in summary_prompt


def test_dq_agent_falls_back_to_all_checks_on_empty_plan(full_db: Path) -> None:
    llm = ScriptedLLM({CheckPlan: [CheckPlan(checks=[])], DQSummary: [SUMMARY]})
    out = run_data_quality(llm, DqRunner(full_db), "Is the data healthy?")
    assert out.plan_fallback and len(out.checks_run) == 7
    assert {f.kind for f in out.findings} >= {"duplicate_events", "late_events", "schema_drift"}


# ---------- investigation: fan-out, merge, classification ----------


def make_investigation(full_db: Path, scope: InvestigationScope, **kw: Any) -> Any:
    llm = ScriptedLLM(
        {
            InvestigationScope: [scope] * 4,
            InvestigationSummary: [InvestigationSummary(answer="concluded", confidence_note="c")]
            * 4,
        }
    )
    graph = build_investigation(llm, DqRunner(full_db), MetricScanner(full_db), **kw)
    return graph, llm


def test_investigation_finds_and_classifies_the_real_demand_drop(full_db: Path) -> None:
    graph, llm = make_investigation(
        full_db, InvestigationScope(focus="revenue", category="Apparel")
    )
    out = investigate(graph, "Why did Apparel orders fall?")
    drops = [f for f in out.findings if f.kind == "demand_drop"]
    assert len(drops) == 1 and drops[0].scope == {"category": "Apparel"}
    assert out.causes[drops[0].finding_id] == "business_change"  # clean data around it
    prompt = llm.calls[-1][1][1].content
    assert "business_change" in prompt and "<untrusted_data" in prompt
    assert set(out.worker_timings_s) == {"revenue", "data_quality"}  # fan-out was scoped


def test_investigation_scope_filters_findings_by_window_and_region(full_db: Path) -> None:
    graph, _ = make_investigation(
        full_db,
        InvestigationScope(
            focus="all", start=date(2026, 3, 1), end=date(2026, 3, 5), region="West"
        ),
    )
    out = investigate(graph, "What happened in West in early March?")
    kinds = {(f.kind, f.scope.get("region")) for f in out.findings}
    assert kinds == {("late_events", "West")}


def test_investigation_all_focus_runs_every_worker_and_sorts_findings(full_db: Path) -> None:
    graph, _ = make_investigation(full_db, InvestigationScope(focus="all"))
    out = investigate(graph, "Anything unusual this year?")
    assert set(out.worker_timings_s) == {"revenue", "cancellations", "data_quality"}
    starts = [(f.start, f.kind) for f in out.findings]
    assert starts == sorted(starts)  # merged order is made deterministic by the synthesizer
    assert {f.kind for f in out.findings} >= {"cancellation_spike", "demand_drop", "late_events"}
    spike = next(f for f in out.findings if f.kind == "cancellation_spike")
    assert out.causes[spike.finding_id] == "business_change"


def test_workers_actually_run_concurrently(full_db: Path) -> None:
    """With each worker taking ~0.4 s, a parallel fan-out must finish in well under the 1.2 s
    a sequential run needs."""

    class SlowScanner(MetricScanner):
        def demand_scan(self) -> list[Any]:
            time.sleep(0.4)
            return []

        def cancellation_scan(self) -> list[Any]:
            time.sleep(0.4)
            return []

    class SlowDq(DqRunner):
        def run_all(self, **params: Any) -> list[Any]:
            time.sleep(0.4)
            return []

    def run(parallel: bool) -> float:
        llm = ScriptedLLM(
            {
                InvestigationScope: [InvestigationScope(focus="all")],
                InvestigationSummary: [InvestigationSummary(answer="a", confidence_note="c")],
            }
        )
        g = build_investigation(llm, SlowDq(full_db), SlowScanner(full_db), parallel=parallel)
        return investigate(g, "q").wall_time_s

    par, seq = run(True), run(False)
    assert seq >= 1.2 and par < 0.9, (par, seq)


def test_invalid_scope_output_falls_back_to_full_scan(full_db: Path) -> None:
    from langchain_core.exceptions import OutputParserException

    class BadScope:
        def with_structured_output(self, schema: Any) -> Any:
            class B:
                def invoke(self, messages: Any, config: Any = None) -> Any:
                    if schema is InvestigationScope:
                        raise OutputParserException("bad")
                    return InvestigationSummary(answer="a", confidence_note="c")

            return B()

    g = build_investigation(BadScope(), DqRunner(full_db), MetricScanner(full_db))
    out = investigate(g, "q")
    assert out.scope_fallback and out.scope.focus == "all" and out.findings


# ---------- calendar in the investigation ----------


def test_calendar_context_is_attached_when_a_window_is_known(full_db: Path) -> None:
    import httpx

    payload = [{"date": "2026-02-16", "name": "Presidents Day", "types": ["Public"]}]
    cal = CalendarClient(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    )
    graph, llm = make_investigation(
        full_db,
        InvestigationScope(focus="revenue", start=date(2026, 2, 10), end=date(2026, 2, 20)),
        calendar=cal,
    )
    out = investigate(graph, "Why was mid February unusual?")
    assert out.calendar and out.calendar["source"] == "api"
    assert "Presidents Day" in llm.calls[-1][1][1].content


# ---------- handoff ----------


def test_corruption_signals() -> None:
    s = corruption_signals(["order_id", "payment_amount"], [["a", 10.0], ["b", -5.0]])
    assert [x.kind for x in s] == ["invalid_payments"]
    d = corruption_signals(["order_id", "event_type"], [["a", "x"], ["a", "x"], ["b", "y"]])
    assert [x.kind for x in d] == ["duplicate_events"]
    assert corruption_signals(["region", "net_revenue"], [["East", 100.0], ["West", 90.0]]) == []
    assert corruption_signals(["n"], [[0]]) == []  # a zero count is not a money column


def test_sql_analyst_hands_off_to_data_quality_and_keeps_both_results(
    runner: SqlRunner, retriever: Retriever
) -> None:
    bad_sql = (
        "SELECT order_id, payment_amount FROM raw_events WHERE event_type = 'PAYMENT_COMPLETED' "
        "AND event_ts >= DATE '2026-05-10' AND event_ts < DATE '2026-05-18' "
        "ORDER BY payment_amount LIMIT 20"
    )
    sup = ScriptedLLM(
        {
            RouteDecision: [
                RouteDecision(
                    intent="metric_question",
                    standalone_question="Lowest payments mid May",
                    plan=["q"],
                )
            ]
        }
    )
    sql = ScriptedLLM(
        {
            SQLDraft: [SQLDraft(sql=bad_sql)],
            SQLSummary: [
                SQLSummary(answer="The lowest payments are shown.", confidence_note="direct")
            ],
        }
    )
    app = build_app(
        supervisor_llm=sup,
        sql_llm=sql,
        knowledge_llm=ScriptedLLM({KnowledgeDraft: []}),
        runner=runner,
        retriever=retriever,
    )
    r = chat(app, "Show the lowest payments in mid May", "t-handoff")
    assert (
        r.handoff
        and r.handoff["to"] == "data_quality"
        and "invalid_payments" in r.handoff["checks"]
    )
    assert r.answer.startswith("The lowest payments are shown.")
    assert "Data quality note" in r.answer and "invalid_payments" in r.answer
    kinds = [e.get("kind", "sql") for e in r.evidence]
    assert kinds == ["sql", "findings"]
    assert r.evidence[1]["findings"][0]["kind"] == "invalid_payments"


def test_clean_result_does_not_hand_off(runner: SqlRunner, retriever: Retriever) -> None:
    sup = ScriptedLLM(
        {
            RouteDecision: [
                RouteDecision(intent="metric_question", standalone_question="q", plan=["q"])
            ]
        }
    )
    sql = ScriptedLLM(
        {
            SQLDraft: [
                SQLDraft(sql="SELECT region, sum(net_revenue) AS rev FROM fct_orders GROUP BY 1")
            ],
            SQLSummary: [SQLSummary(answer="ok", confidence_note="c")],
        }
    )
    app = build_app(
        supervisor_llm=sup,
        sql_llm=sql,
        knowledge_llm=ScriptedLLM({KnowledgeDraft: []}),
        runner=runner,
        retriever=retriever,
    )
    assert chat(app, "Revenue by region", "t-nohand").handoff is None


# ---------- agents-as-tools ----------


def test_sql_analyst_calls_knowledge_as_a_tool_and_keeps_control(
    runner: SqlRunner, retriever: Retriever
) -> None:
    kn = ScriptedLLM(
        {
            KnowledgeDraft: [
                KnowledgeDraft(
                    answerable=True,
                    answer="AOV is gross revenue divided by paid orders.",
                    citations=["metric_definitions#average-order-value-aov"],
                )
            ]
        }
    )
    asked: list[str] = []
    tool = make_ask_knowledge_tool(kn, retriever, calls_log=asked)
    sql = ScriptedLLM(
        {
            SQLDraft: [
                SQLDraft(lookup_question="How is average order value calculated?"),
                SQLDraft(sql="SELECT sum(gross_revenue) / count(paid_ts) AS aov FROM fct_orders"),
            ],
            SQLSummary: [SQLSummary(answer="AOV computed.", confidence_note="direct")],
        }
    )
    graph = build_sql_analyst(
        sql, runner, ask_knowledge=lambda q: str(tool.invoke({"question": q}))
    )
    out = ask_sql(graph, "What is AOV?")
    assert (
        out.status == "ok"
        and out.lookups == 1
        and asked == ["How is average order value calculated?"]
    )
    second_prompt = sql.calls[1][1][1].content
    assert "gross revenue divided by paid orders" in second_prompt
    assert "<untrusted_data" in second_prompt  # the tool's answer is treated as data
    assert out.evidence is not None and out.answer == "AOV computed."


def test_lookup_budget_is_enforced(retriever: Retriever) -> None:
    kn = ScriptedLLM(
        {
            KnowledgeDraft: [
                KnowledgeDraft(
                    answerable=True,
                    answer="x",
                    citations=["metric_definitions#average-order-value-aov"],
                )
            ]
            * 3
        }
    )
    tool = make_ask_knowledge_tool(kn, retriever, max_calls=1)
    tool.invoke({"question": "How is average order value calculated?"})
    with pytest.raises(KnowledgeBudgetExceeded):
        tool.invoke({"question": "How is average order value calculated?"})


def test_endless_lookup_requests_end_in_a_bounded_failure(runner: SqlRunner) -> None:
    sql = ScriptedLLM({SQLDraft: [SQLDraft(lookup_question="what?")] * 6, SQLSummary: []})
    graph = build_sql_analyst(sql, runner, ask_knowledge=lambda q: "an answer", max_retries=1)
    out = ask_sql(graph, "Anything")
    assert out.status == "failed" and out.lookups == 2 and len(sql.calls) <= 6


def test_without_a_knowledge_tool_lookup_requests_cannot_loop(runner: SqlRunner) -> None:
    sql = ScriptedLLM({SQLDraft: [SQLDraft(lookup_question="what?")] * 4, SQLSummary: []})
    out = ask_sql(build_sql_analyst(sql, runner), "Anything")
    assert out.status == "failed" and out.lookups == 0
