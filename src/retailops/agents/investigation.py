"""Anomaly investigation: scope -> parallel fan-out (Send) -> merge -> synthesize.

Three tool-driven workers run at the same time: revenue/demand scan, cancellation scan and the
data-quality suite (plus an optional calendar lookup). They write findings into a state key with
an `operator.add` reducer, so the merge is a list concatenation whose ORDER IS NOT GUARANTEED
(branches finish in any order); the synthesizer therefore sorts findings before use.

Workers are deterministic tools rather than free-running model agents, which makes detection
measurable without a model (see evals/run_detectors.py). The model only (a) turns the question
into a scope and (b) writes the conclusion from the structured findings.
"""

from __future__ import annotations

import json
import operator
import time
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send
from pydantic import BaseModel, ValidationError

from retailops.guards.injection import spotlight
from retailops.observability import log_event, run_config
from retailops.prompts import load_prompt
from retailops.tools.calendar import CalendarClient
from retailops.tools.dq import DQ_KINDS, DqRunner, Finding, classify_cause, overlap
from retailops.tools.metrics import MetricScanner

Focus = Literal["revenue", "cancellations", "data_quality", "all"]
Region = Literal["North", "South", "East", "West"]
Category = Literal["Electronics", "Apparel", "Home", "Beauty", "Sports", "Grocery"]


class InvestigationScope(BaseModel):
    focus: Focus = "all"
    start: date | None = None
    end: date | None = None
    region: Region | None = None
    category: Category | None = None


class InvestigationSummary(BaseModel):
    answer: str
    confidence_note: str


class InvestigationOutput(BaseModel):
    answer: str
    confidence_note: str
    scope: InvestigationScope
    scope_fallback: bool
    findings: list[Finding]
    causes: dict[str, str]  # finding_id -> data_quality | business_change
    calendar: dict[str, Any] | None
    worker_timings_s: dict[str, float]
    wall_time_s: float


class InvState(TypedDict, total=False):
    question: str
    context: str
    scope: dict[str, Any]
    scope_fallback: bool
    findings: Annotated[list[dict[str, Any]], operator.add]
    timings: Annotated[list[dict[str, Any]], operator.add]
    calendar: dict[str, Any] | None
    answer: str
    confidence_note: str
    causes: dict[str, str]
    final_findings: list[dict[str, Any]]
    started: float


def _in_scope(f: Finding, scope: InvestigationScope) -> bool:
    if scope.start and f.end < scope.start:
        return False
    if scope.end and f.start > scope.end:
        return False
    if scope.region and f.scope.get("region") not in (None, scope.region):
        return False
    return not (scope.category and f.scope.get("category") not in (None, scope.category))


def build_investigation(
    llm: Any,
    dq: DqRunner,
    scanner: MetricScanner,
    calendar: CalendarClient | None = None,
    *,
    parallel: bool = True,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    scoper = llm.with_structured_output(InvestigationScope)
    summarizer = llm.with_structured_output(InvestigationSummary)

    def plan(state: InvState, config: RunnableConfig) -> dict[str, Any]:
        human = (
            f"Conversation so far:\n{state['context']}\n\n" if state.get("context") else ""
        ) + f"Question: {state['question']}"
        fallback = False
        try:
            scope = InvestigationScope.model_validate(
                scoper.invoke(
                    [SystemMessage(load_prompt("investigation_scope")), HumanMessage(human)],
                    config=config,
                )
            )
        except (ValidationError, OutputParserException) as e:
            log_event("investigation_scope_invalid", error=str(e)[:200])
            scope, fallback = InvestigationScope(), True
        return {
            "scope": scope.model_dump(mode="json"),
            "scope_fallback": fallback,
            "started": time.perf_counter(),
        }

    def _timed(name: str, fn: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        found: list[Finding] = fn()
        return {
            "findings": [f.model_dump(mode="json") for f in found],
            "timings": [{"worker": name, "elapsed_s": round(time.perf_counter() - t0, 4)}],
        }

    def revenue_slice(state: InvState) -> dict[str, Any]:
        return _timed("revenue", scanner.demand_scan)

    def cancellation_slice(state: InvState) -> dict[str, Any]:
        return _timed("cancellations", scanner.cancellation_scan)

    def dq_slice(state: InvState) -> dict[str, Any]:
        return _timed("data_quality", lambda: [f for r in dq.run_all() for f in r.findings])

    def calendar_slice(state: InvState) -> dict[str, Any]:
        scope = InvestigationScope.model_validate(state["scope"])
        if calendar is None or not (scope.start and scope.end):
            return {"calendar": None}
        ctx = calendar.get_context(scope.start, scope.end)
        return {"calendar": ctx.model_dump(mode="json")}

    workers: dict[str, Any] = {
        "revenue_slice": revenue_slice,
        "cancellation_slice": cancellation_slice,
        "dq_slice": dq_slice,
        "calendar_slice": calendar_slice,
    }

    def selected(state: InvState) -> list[str]:
        focus = state["scope"]["focus"]
        names = {
            "revenue": ["revenue_slice", "dq_slice"],  # always check data quality alongside
            "cancellations": ["cancellation_slice", "dq_slice"],
            "data_quality": ["dq_slice"],
            "all": ["revenue_slice", "cancellation_slice", "dq_slice"],
        }[focus]
        return [*names, "calendar_slice"]

    def fan_out(state: InvState) -> list[Send]:
        return [Send(name, state) for name in selected(state)]

    def synthesize(state: InvState, config: RunnableConfig) -> dict[str, Any]:
        scope = InvestigationScope.model_validate(state["scope"])
        everything = [Finding.model_validate(f) for f in state.get("findings", [])]
        in_scope = sorted(
            (f for f in everything if _in_scope(f, scope)),
            key=lambda f: (f.start, f.kind, f.finding_id),  # branch order is arbitrary
        )
        dq_findings = [f for f in everything if f.kind in DQ_KINDS]
        causes = {f.finding_id: classify_cause(f, dq_findings) for f in in_scope}
        payload = {
            "scope": scope.model_dump(mode="json"),
            "findings": [
                {**f.model_dump(mode="json", exclude={"sql"}), "cause_class": causes[f.finding_id]}
                for f in in_scope
            ],
            "overlapping_data_quality_findings": {
                f.finding_id: [d.finding_id for d in dq_findings if overlap(f, d)]
                for f in in_scope
                if f.kind not in DQ_KINDS
            },
            "calendar": state.get("calendar"),
        }
        human = f"Question: {state['question']}\n\n" + spotlight(
            json.dumps(payload), source="investigation_tools"
        )
        out: InvestigationSummary = summarizer.invoke(
            [SystemMessage(load_prompt("investigation_summarize")), HumanMessage(human)],
            config=config,
        )
        return {
            "answer": out.answer,
            "confidence_note": out.confidence_note,
            "causes": causes,
            "final_findings": [f.model_dump(mode="json") for f in in_scope],
        }

    def gated(name: str, fn: Any) -> Any:
        """Sequential control: skip workers the scope did not select, like the fan-out does."""

        def run(state: InvState) -> dict[str, Any]:
            result: dict[str, Any] = fn(state) if name in selected(state) else {}
            return result

        return run

    g = StateGraph(InvState)
    g.add_node("plan", plan)
    for name, fn in workers.items():
        g.add_node(name, fn if parallel else gated(name, fn))
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "plan")
    if parallel:
        g.add_conditional_edges("plan", fan_out, list(workers))
        for name in workers:
            g.add_edge(name, "synthesize")
    else:
        # Sequential control for the latency comparison: same workers, one after another.
        order = ["revenue_slice", "cancellation_slice", "dq_slice", "calendar_slice"]
        g.add_edge("plan", order[0])
        for a, b in zip(order, order[1:], strict=False):
            g.add_edge(a, b)
        g.add_edge(order[-1], "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


def investigate(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    question: str,
    *,
    context: str = "",
    thread_id: str | None = None,
    scenario_id: str | None = None,
) -> InvestigationOutput:
    t0 = time.perf_counter()
    config = run_config("investigation", thread_id=thread_id, scenario_id=scenario_id)
    final = graph.invoke({"question": question, "context": context}, config=config)  # type: ignore[arg-type]
    timings = {t["worker"]: t["elapsed_s"] for t in final.get("timings", [])}
    return InvestigationOutput(
        answer=final["answer"],
        confidence_note=final["confidence_note"],
        scope=InvestigationScope.model_validate(final["scope"]),
        scope_fallback=final.get("scope_fallback", False),
        findings=[Finding.model_validate(f) for f in final.get("final_findings", [])],
        causes=final.get("causes", {}),
        calendar=final.get("calendar"),
        worker_timings_s=timings,
        wall_time_s=round(time.perf_counter() - t0, 4),
    )
