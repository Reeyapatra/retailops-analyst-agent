"""Top-level LangGraph app: intake -> supervisor -> specialist(s) -> finalize, with memory.

Specialists: SQL Analyst (metric questions), Knowledge (definitions/runbooks, with citations),
Data Quality (named checks), Investigation (parallel fan-out over scanners and DQ checks).

Coordination patterns:
- supervisor routing: the supervisor picks one specialist from a validated intent;
- handoff: after a SQL answer, deterministic corruption signals (zero/negative money values,
  duplicate rows) transfer control to Data Quality with a structured reason via `Command`;
- agents-as-tools: the SQL Analyst can call the Knowledge agent as the `ask_knowledge` tool for
  a definition, within a call budget, without giving up control.

NOT built yet (README status table): verifier, action proposer and human approval, API/UI.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Overwrite
from pydantic import BaseModel

from retailops.agents.data_quality import run_data_quality, run_targeted_checks
from retailops.agents.investigation import build_investigation, investigate
from retailops.agents.knowledge import answer_knowledge_question
from retailops.agents.sql_analyst import ask_sql, build_sql_analyst
from retailops.agents.supervisor import route
from retailops.observability import log_event, new_trace_id, run_config
from retailops.rag.retriever import Retriever
from retailops.tools.calendar import CalendarClient
from retailops.tools.dq import DqRunner
from retailops.tools.knowledge_tool import make_ask_knowledge_tool
from retailops.tools.metrics import MetricScanner
from retailops.tools.signals import corruption_signals
from retailops.tools.sql import SqlRunner

MAX_INPUT_CHARS = 2000
HISTORY_TURNS = 6
HISTORY_CHARS = 400

DECLINE = (
    "I can help with retail order metrics, data quality questions, anomaly investigations, and "
    "our metric definitions, runbooks and policies. I can't help with that request."
)


class RetailOpsState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_input: str
    intent: str
    standalone_question: str
    plan: list[str]
    route_fallback: bool
    findings: Annotated[list[dict[str, Any]], operator.add]
    evidence: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    handoff: dict[str, Any] | None
    final_answer: str
    confidence_note: str
    status: str
    sql_attempts: int | None
    last_failure_stage: str | None


class ChatResponse(BaseModel):
    thread_id: str
    answer: str
    intent: str
    status: str
    confidence_note: str
    evidence: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    route_fallback: bool
    sql_attempts: int | None = None
    last_failure_stage: str | None = None  # stage of the last unsuccessful SQL attempt, if any
    handoff: dict[str, Any] | None = None


def render_history(messages: list[AnyMessage]) -> str:
    lines = []
    for m in messages[-2 * HISTORY_TURNS :]:
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        text = str(m.content)
        lines.append(f"{role}: {text[:HISTORY_CHARS]}")
    return "\n".join(lines)


def _thread_id(config: RunnableConfig) -> str | None:
    value = config.get("configurable", {}).get("thread_id")
    return str(value) if value else None


def build_app(
    *,
    supervisor_llm: Any,
    sql_llm: Any,
    knowledge_llm: Any,
    runner: SqlRunner,
    retriever: Retriever,
    dq_llm: Any | None = None,
    investigation_llm: Any | None = None,
    calendar: CalendarClient | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    dq = DqRunner(runner.db_path)
    scanner = MetricScanner(runner.db_path)
    dq_model = dq_llm or sql_llm
    investigation_graph = build_investigation(investigation_llm or sql_llm, dq, scanner, calendar)

    def intake(state: RetailOpsState) -> dict[str, Any]:
        text = str(state["messages"][-1].content).strip()
        reset: dict[str, Any] = {
            "findings": Overwrite([]),
            "evidence": [],
            "citations": [],
            "route_fallback": False,
            "final_answer": "",
            "confidence_note": "",
            "sql_attempts": None,
            "last_failure_stage": None,
            "handoff": None,
        }
        if not text:
            return {
                **reset,
                "status": "rejected",
                "intent": "out_of_scope",
                "final_answer": "Please enter a question.",
            }
        if len(text) > MAX_INPUT_CHARS:
            return {
                **reset,
                "status": "rejected",
                "intent": "out_of_scope",
                "final_answer": f"Please keep questions under {MAX_INPUT_CHARS} characters.",
            }
        return {**reset, "user_input": text, "status": "ok"}

    def supervisor(state: RetailOpsState, config: RunnableConfig) -> dict[str, Any]:
        decision, fallback = route(
            supervisor_llm,
            state["user_input"],
            render_history(state["messages"][:-1]),
            thread_id=_thread_id(config),
        )
        log_event("routed", intent=decision.intent, fallback=fallback)
        return {
            "intent": decision.intent,
            "standalone_question": decision.standalone_question,
            "plan": decision.plan,
            "route_fallback": fallback,
        }

    def sql_analyst(
        state: RetailOpsState, config: RunnableConfig
    ) -> Command[Literal["data_quality", "finalize"]]:
        # Agents-as-tools: a per-request tool with its own call budget.
        knowledge_tool = make_ask_knowledge_tool(knowledge_llm, retriever)
        graph = build_sql_analyst(
            sql_llm, runner, ask_knowledge=lambda q: str(knowledge_tool.invoke({"question": q}))
        )
        out = ask_sql(
            graph,
            state["standalone_question"],
            context=render_history(state["messages"][:-1]),
            thread_id=_thread_id(config),
        )
        update: dict[str, Any] = {
            "findings": [{"agent": "sql_analyst", "status": out.status, "answer": out.answer}],
            "evidence": [out.evidence.model_dump()] if out.evidence else [],
            "final_answer": out.answer,
            "confidence_note": out.confidence_note,
            "status": out.status,
            "sql_attempts": len(out.attempts),
            "last_failure_stage": (
                next((a.stage for a in reversed(out.attempts) if a.stage != "ok"), None)
                if out.status != "ok"
                else None
            ),
        }
        signals = (
            corruption_signals(out.evidence.columns, out.evidence.rows) if out.evidence else []
        )
        if signals:
            update["handoff"] = {
                "from": "sql_analyst",
                "to": "data_quality",
                "reason": "; ".join(s.reason for s in signals),
                "checks": [s.kind for s in signals],
            }
            log_event("handoff", **update["handoff"])
            return Command(update=update, goto="data_quality")
        return Command(update=update, goto="finalize")

    def knowledge(state: RetailOpsState, config: RunnableConfig) -> dict[str, Any]:
        out = answer_knowledge_question(
            knowledge_llm,
            retriever,
            state["standalone_question"],
            context=render_history(state["messages"][:-1]),
            thread_id=_thread_id(config),
        )
        return {
            "findings": [{"agent": "knowledge", "status": out.status, "answer": out.answer}],
            "citations": [c.model_dump() for c in out.citations],
            "final_answer": out.answer,
            "confidence_note": out.confidence_note,
            "status": out.status,
        }

    def data_quality(state: RetailOpsState, config: RunnableConfig) -> dict[str, Any]:
        handoff = state.get("handoff")
        if handoff:  # transferred from the SQL Analyst: deterministic follow-up checks only
            findings, note = run_targeted_checks(dq, handoff["checks"], [handoff["reason"]])
            return {
                "findings": [{"agent": "data_quality", "status": "handoff", "answer": note}],
                "evidence": [
                    *state.get("evidence", []),
                    {"kind": "findings", "findings": [f.model_dump(mode="json") for f in findings]},
                ],
                "final_answer": f"{state['final_answer']}\n\n{note}",
                "confidence_note": "SQL result plus follow-up data quality checks.",
            }
        out = run_data_quality(
            dq_model,
            dq,
            state["standalone_question"],
            render_history(state["messages"][:-1]),
            thread_id=_thread_id(config),
        )
        return {
            "findings": [{"agent": "data_quality", "status": "ok", "answer": out.answer}],
            "evidence": [
                {
                    "kind": "findings",
                    "checks_run": out.checks_run,
                    "findings": [f.model_dump(mode="json") for f in out.findings],
                }
            ],
            "final_answer": out.answer,
            "confidence_note": out.confidence_note,
            "status": "ok",
        }

    def investigation(state: RetailOpsState, config: RunnableConfig) -> dict[str, Any]:
        out = investigate(
            investigation_graph,
            state["standalone_question"],
            context=render_history(state["messages"][:-1]),
            thread_id=_thread_id(config),
        )
        return {
            "findings": [{"agent": "investigation", "status": "ok", "answer": out.answer}],
            "evidence": [
                {
                    "kind": "findings",
                    "scope": out.scope.model_dump(mode="json"),
                    "causes": out.causes,
                    "calendar": out.calendar,
                    "findings": [f.model_dump(mode="json") for f in out.findings],
                    "worker_timings_s": out.worker_timings_s,
                }
            ],
            "final_answer": out.answer,
            "confidence_note": out.confidence_note,
            "status": "ok",
        }

    def decline(state: RetailOpsState) -> dict[str, Any]:
        return {"final_answer": DECLINE, "status": "declined", "confidence_note": "Out of scope."}

    def finalize(state: RetailOpsState) -> dict[str, Any]:
        return {"messages": [AIMessage(state["final_answer"])]}

    def after_intake(state: RetailOpsState) -> Literal["supervisor", "finalize"]:
        return "finalize" if state["status"] == "rejected" else "supervisor"

    def after_supervisor(
        state: RetailOpsState,
    ) -> Literal["sql_analyst", "knowledge", "data_quality", "investigation", "decline"]:
        return {
            "metric_question": "sql_analyst",
            "data_quality": "data_quality",
            "knowledge_question": "knowledge",
            "anomaly_investigation": "investigation",
            "out_of_scope": "decline",
        }[state["intent"]]  # type: ignore[return-value]

    g = StateGraph(RetailOpsState)
    for name, fn in [
        ("intake", intake),
        ("supervisor", supervisor),
        ("sql_analyst", sql_analyst),
        ("knowledge", knowledge),
        ("data_quality", data_quality),
        ("investigation", investigation),
        ("decline", decline),
        ("finalize", finalize),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", after_intake)
    g.add_conditional_edges("supervisor", after_supervisor)
    # sql_analyst routes itself with Command (finalize, or a handoff to data_quality)
    for n in ("knowledge", "data_quality", "investigation", "decline"):
        g.add_edge(n, "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def chat(
    app: CompiledStateGraph[Any, Any, Any, Any],
    message: str,
    thread_id: str,
    *,
    scenario_id: str | None = None,
    callbacks: list[Any] | None = None,
) -> ChatResponse:
    """Run one conversation turn on a thread. State persists via the app's checkpointer."""
    new_trace_id()
    config = run_config("retailops_chat", thread_id=thread_id, scenario_id=scenario_id)
    config["configurable"] = {"thread_id": thread_id}
    if callbacks:
        config["callbacks"] = callbacks
    final = app.invoke({"messages": [HumanMessage(message)]}, config=config)  # type: ignore[arg-type]
    return ChatResponse(
        thread_id=thread_id,
        answer=final["final_answer"],
        intent=final["intent"],
        status=final["status"],
        confidence_note=final.get("confidence_note", ""),
        evidence=final.get("evidence", []),
        citations=final.get("citations", []),
        route_fallback=final.get("route_fallback", False),
        sql_attempts=final.get("sql_attempts"),
        last_failure_stage=final.get("last_failure_stage"),
        handoff=final.get("handoff"),
    )
