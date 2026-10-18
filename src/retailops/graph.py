"""Top-level LangGraph app (M3): intake -> supervisor -> specialist -> finalize, with memory.

Built in M3: SQL Analyst and Knowledge specialists, thread memory via a checkpointer.
NOT built yet (see README status table): parallel anomaly investigation, data-quality subgraph,
verifier, action proposer and human approval. `anomaly_investigation` therefore returns an
explicit "not available" answer instead of guessing, and `data_quality` questions are answered
by the SQL Analyst over the raw/staging tables.
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
from langgraph.types import Overwrite
from pydantic import BaseModel

from retailops.agents.knowledge import answer_knowledge_question
from retailops.agents.sql_analyst import ask_sql, build_sql_analyst
from retailops.agents.supervisor import route
from retailops.observability import log_event, new_trace_id, run_config
from retailops.rag.retriever import Retriever
from retailops.tools.sql import SqlRunner

MAX_INPUT_CHARS = 2000
HISTORY_TURNS = 6
HISTORY_CHARS = 400

DECLINE = (
    "I can help with retail order metrics, data quality questions, and our metric definitions, "
    "runbooks and policies. I can't help with that request."
)
UNAVAILABLE = (
    "Root-cause investigation of anomalies is not available in this build yet. "
    "You can ask for specific metrics (for example cancellation rate by category and region over "
    "a date range) or ask what a runbook recommends."
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
    final_answer: str
    confidence_note: str
    status: str


class ChatResponse(BaseModel):
    thread_id: str
    answer: str
    intent: str
    status: str
    confidence_note: str
    evidence: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    route_fallback: bool


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
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    sql_graph = build_sql_analyst(sql_llm, runner)

    def intake(state: RetailOpsState) -> dict[str, Any]:
        text = str(state["messages"][-1].content).strip()
        reset: dict[str, Any] = {
            "findings": Overwrite([]),
            "evidence": [],
            "citations": [],
            "route_fallback": False,
            "final_answer": "",
            "confidence_note": "",
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

    def sql_analyst(state: RetailOpsState, config: RunnableConfig) -> dict[str, Any]:
        out = ask_sql(
            sql_graph,
            state["standalone_question"],
            context=render_history(state["messages"][:-1]),
            thread_id=_thread_id(config),
        )
        return {
            "findings": [{"agent": "sql_analyst", "status": out.status, "answer": out.answer}],
            "evidence": [out.evidence.model_dump()] if out.evidence else [],
            "final_answer": out.answer,
            "confidence_note": out.confidence_note,
            "status": out.status,
        }

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

    def unavailable(state: RetailOpsState) -> dict[str, Any]:
        return {
            "final_answer": UNAVAILABLE,
            "status": "unsupported",
            "confidence_note": "No analysis was performed.",
        }

    def decline(state: RetailOpsState) -> dict[str, Any]:
        return {"final_answer": DECLINE, "status": "declined", "confidence_note": "Out of scope."}

    def finalize(state: RetailOpsState) -> dict[str, Any]:
        return {"messages": [AIMessage(state["final_answer"])]}

    def after_intake(state: RetailOpsState) -> Literal["supervisor", "finalize"]:
        return "finalize" if state["status"] == "rejected" else "supervisor"

    def after_supervisor(
        state: RetailOpsState,
    ) -> Literal["sql_analyst", "knowledge", "unavailable", "decline"]:
        return {
            "metric_question": "sql_analyst",
            "data_quality": "sql_analyst",
            "knowledge_question": "knowledge",
            "anomaly_investigation": "unavailable",
            "out_of_scope": "decline",
        }[state["intent"]]  # type: ignore[return-value]

    g = StateGraph(RetailOpsState)
    for name, fn in [
        ("intake", intake),
        ("supervisor", supervisor),
        ("sql_analyst", sql_analyst),
        ("knowledge", knowledge),
        ("unavailable", unavailable),
        ("decline", decline),
        ("finalize", finalize),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", after_intake)
    g.add_conditional_edges("supervisor", after_supervisor)
    for n in ("sql_analyst", "knowledge", "unavailable", "decline"):
        g.add_edge(n, "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def chat(
    app: CompiledStateGraph[Any, Any, Any, Any],
    message: str,
    thread_id: str,
    *,
    scenario_id: str | None = None,
) -> ChatResponse:
    """Run one conversation turn on a thread. State persists via the app's checkpointer."""
    new_trace_id()
    config = run_config("retailops_chat", thread_id=thread_id, scenario_id=scenario_id)
    config["configurable"] = {"thread_id": thread_id}
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
    )
