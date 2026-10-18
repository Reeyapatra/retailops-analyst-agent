"""SQL Analyst subgraph: schema -> generate SQL -> guard -> execute -> sanity check -> summarize.

Failures at any stage (guard rejection, execution error, empty or all-NULL result) feed a
structured message back to the generator for a bounded number of retries (FR-2).
The language model is injected, so tests run with a scripted model and production passes a real
chat model (it only needs `.with_structured_output(Schema).invoke(messages, config=...)`).
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from retailops.guards.injection import spotlight
from retailops.observability import log_event, run_config
from retailops.prompts import load_prompt
from retailops.state import (
    EVIDENCE_SAMPLE_ROWS,
    SQLAnalystOutput,
    SQLAttempt,
    SQLEvidence,
)
from retailops.tools.sql import RunSqlResult, SqlRunner, get_schema

DEFAULT_MAX_RETRIES = 2


class SQLDraft(BaseModel):
    """Structured output of the generator."""

    sql: str
    rationale: str = ""


class SQLSummary(BaseModel):
    """Structured output of the summarizer."""

    answer: str
    confidence_note: str


class SQLState(TypedDict, total=False):
    question: str
    context: str
    schema_text: str
    feedback: str
    draft_sql: str
    result: dict[str, Any]
    attempts: Annotated[list[dict[str, Any]], operator.add]
    status: str
    answer: str
    confidence_note: str


def _attempt(sql: str, stage: str, error: str | None = None) -> list[dict[str, Any]]:
    return [SQLAttempt(sql=sql, stage=stage, error=error).model_dump()]  # type: ignore[arg-type]


def build_sql_analyst(
    llm: Any, runner: SqlRunner, *, max_retries: int = DEFAULT_MAX_RETRIES
) -> CompiledStateGraph[Any, Any, Any, Any]:
    generator = llm.with_structured_output(SQLDraft)
    summarizer = llm.with_structured_output(SQLSummary)
    max_attempts = 1 + max_retries

    def load_schema(state: SQLState) -> dict[str, Any]:
        return {"schema_text": get_schema(), "attempts": []}

    def generate(state: SQLState, config: RunnableConfig) -> dict[str, Any]:
        system = load_prompt("sql_generate").replace("{schema}", state["schema_text"])
        parts = [f"Question: {state['question']}"]
        if state.get("context"):
            parts.append(f"Conversation context (for resolving follow-ups):\n{state['context']}")
        if state.get("feedback"):
            parts.append(
                "Your previous query failed. Fix it and return a corrected query.\n"
                + state["feedback"]
            )
        draft = generator.invoke(
            [SystemMessage(system), HumanMessage("\n\n".join(parts))], config=config
        )
        return {"draft_sql": draft.sql, "feedback": ""}

    def execute(state: SQLState) -> dict[str, Any]:
        sql = state["draft_sql"]
        result = runner.run(sql)
        update: dict[str, Any] = {"result": result.model_dump()}
        if not result.ok:
            stage = "guard" if result.error_code == "GUARD_REJECTED" else "execute"
            update["attempts"] = _attempt(sql, stage, result.error_message)
            update["feedback"] = (
                f"SQL: {sql}\nProblem ({result.error_code}): {result.error_message}"
            )
        return update

    def check_result(state: SQLState) -> dict[str, Any]:
        result = RunSqlResult.model_validate(state["result"])
        sql = state["draft_sql"]
        problem: str | None = None
        if result.row_count == 0:
            problem = (
                "The query ran but returned no rows. Check filters, date ranges and "
                "spelling of category/region values."
            )
        elif result.row_count == 1 and all(v is None for v in result.rows[0]):
            problem = "The query returned only NULL values. Check joins and filters."
        if problem is None:
            return {"attempts": _attempt(sql, "ok"), "status": "ok"}
        return {
            "attempts": _attempt(sql, "check", problem),
            "feedback": f"SQL: {sql}\nProblem (SUSPICIOUS_RESULT): {problem}",
            "status": "empty_result" if result.row_count == 0 else "suspicious",
        }

    def summarize(state: SQLState, config: RunnableConfig) -> dict[str, Any]:
        result = RunSqlResult.model_validate(state["result"])
        sample = result.rows[:EVIDENCE_SAMPLE_ROWS]
        data = json.dumps({"columns": result.columns, "rows": sample}, default=str)
        human = (
            f"Question: {state['question']}\n\nSQL used:\n{result.sql_executed}\n\n"
            f"Rows returned: {result.row_count}"
            f"{' (truncated to the row cap)' if result.truncated else ''}.\n\n"
            + spotlight(data, source="run_sql")
        )
        out = summarizer.invoke(
            [SystemMessage(load_prompt("sql_summarize")), HumanMessage(human)], config=config
        )
        status = "empty_result" if result.row_count == 0 else "ok"
        return {"answer": out.answer, "confidence_note": out.confidence_note, "status": status}

    def give_up(state: SQLState) -> dict[str, Any]:
        last = state["attempts"][-1] if state.get("attempts") else {}
        return {
            "status": "failed",
            "answer": (
                "I could not produce a valid query for this question after "
                f"{len(state.get('attempts', []))} attempts. Last problem: {last.get('error')}"
            ),
            "confidence_note": "No result; nothing here should be relied on.",
        }

    def after_execute(state: SQLState) -> Literal["check_result", "generate", "give_up"]:
        if state["result"]["ok"]:
            return "check_result"
        return "generate" if len(state["attempts"]) < max_attempts else "give_up"

    def after_check(state: SQLState) -> Literal["summarize", "generate"]:
        if state["status"] == "ok":
            return "summarize"
        # suspicious or empty: retry while attempts remain, otherwise report what we have
        return "generate" if len(state["attempts"]) < max_attempts else "summarize"

    g = StateGraph(SQLState)
    g.add_node("load_schema", load_schema)
    g.add_node("generate", generate)
    g.add_node("execute", execute)
    g.add_node("check_result", check_result)
    g.add_node("summarize", summarize)
    g.add_node("give_up", give_up)
    g.add_edge(START, "load_schema")
    g.add_edge("load_schema", "generate")
    g.add_edge("generate", "execute")
    g.add_conditional_edges("execute", after_execute)
    g.add_conditional_edges("check_result", after_check)
    g.add_edge("summarize", END)
    g.add_edge("give_up", END)
    return g.compile()


def ask_sql(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    question: str,
    *,
    context: str = "",
    scenario_id: str | None = None,
    thread_id: str | None = None,
) -> SQLAnalystOutput:
    """Run the subgraph and return a typed result with evidence."""
    config = run_config(
        "sql_analyst",
        thread_id=thread_id,
        scenario_id=scenario_id,
    )
    final = graph.invoke({"question": question, "context": context}, config=config)  # type: ignore[arg-type]
    attempts = [SQLAttempt.model_validate(a) for a in final.get("attempts", [])]
    result = RunSqlResult.model_validate(final["result"]) if final.get("result") else None
    evidence = None
    if result is not None and result.ok and final["status"] != "failed":
        evidence = SQLEvidence(
            sql=result.sql_executed or "",
            columns=result.columns,
            rows=result.rows[:EVIDENCE_SAMPLE_ROWS],
            row_count=result.row_count,
            truncated=result.truncated,
        )
    log_event(
        "sql_analyst_done",
        status=final["status"],
        attempts=len(attempts),
        scenario_id=scenario_id,
    )
    return SQLAnalystOutput(
        status=final["status"],
        answer=final["answer"],
        confidence_note=final["confidence_note"],
        evidence=evidence,
        attempts=attempts,
    )
