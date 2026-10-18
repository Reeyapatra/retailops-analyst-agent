"""Systems under test. Both return the same output dict so they are scored identically.

E0: one ReAct-style agent (langchain `create_agent`) holding every tool.
E1: the multi-agent graph (supervisor -> SQL Analyst / Knowledge) as built in M3.

Output keys: answer, final_rows, sql, status, intent, error, sql_attempts, last_failure_stage,
tools_used, retrieved_ids, cited_ids, latency_s, tokens.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from retailops.config import ConfigError
from retailops.graph import build_app, chat
from retailops.observability import run_config
from retailops.prompts import load_prompt
from retailops.rag.retriever import Retriever
from retailops.tools.retrieval import make_search_knowledge_tool
from retailops.tools.sql import RunSqlResult, SqlRunner, get_schema

Target = Callable[[dict[str, Any]], dict[str, Any]]
E0_RECURSION_LIMIT = 25


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        ).strip()
    return str(content)


def _tokens(handler: UsageMetadataCallbackHandler) -> dict[str, int]:
    total = {"input": 0, "output": 0}
    for usage in handler.usage_metadata.values():
        total["input"] += int(usage.get("input_tokens", 0))
        total["output"] += int(usage.get("output_tokens", 0))
    return total


def _error_output(e: Exception, started: float) -> dict[str, Any]:
    return {
        "answer": "",
        "final_rows": None,
        "sql": None,
        "status": "error",
        "intent": None,
        "error": f"{type(e).__name__}: {str(e)[:300]}",
        "sql_attempts": None,
        "last_failure_stage": None,
        "tools_used": [],
        "retrieved_ids": [],
        "cited_ids": [],
        "latency_s": round(time.perf_counter() - started, 3),
        "tokens": {"input": 0, "output": 0},
    }


def build_e0(llm: Any, runner: SqlRunner, retriever: Retriever) -> Target:
    from langchain.agents import create_agent

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        sql_results: list[tuple[str, RunSqlResult]] = []
        retrieved: list[str] = []

        @tool
        def get_schema_tool(tables: list[str] | None = None) -> str:
            """Describe warehouse tables and columns. Pass table names or omit for all."""
            return get_schema(tables)

        get_schema_tool.name = "get_schema"

        @tool
        def run_sql(query: str) -> str:
            """Run one read-only SELECT on the retail warehouse. Returns JSON rows or an error."""
            from retailops.guards.injection import spotlight

            result = runner.run(query)
            sql_results.append((query, result))
            return spotlight(result.model_dump_json(), source="run_sql")

        tools = [get_schema_tool, run_sql, make_search_knowledge_tool(retriever, retrieved)]
        handler = UsageMetadataCallbackHandler()
        try:
            agent = create_agent(llm, tools, system_prompt=load_prompt("baseline_agent"))
            config = run_config("baseline_agent_e0", scenario_id=f"sql_gold:{inputs.get('id', '')}")
            config["callbacks"] = [handler]
            config["recursion_limit"] = E0_RECURSION_LIMIT
            final = agent.invoke({"messages": [("user", inputs["question"])]}, config=config)
        except ConfigError:
            raise
        except Exception as e:
            return _error_output(e, started)

        messages: list[BaseMessage] = final["messages"]
        used = [c["name"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]
        answer = next(
            (
                _text(m.content)
                for m in reversed(messages)
                if isinstance(m, AIMessage) and not m.tool_calls
            ),
            "",
        )
        ok = [(q, r) for q, r in sql_results if r.ok]
        last_sql, last = ok[-1] if ok else (None, None)
        failed = [r for _, r in sql_results if not r.ok]
        stage = None
        if last is None and failed:
            stage = "guard" if failed[-1].error_code == "GUARD_REJECTED" else "execute"
        return {
            "answer": answer,
            "final_rows": last.rows if last else None,
            "sql": last.sql_executed if last else last_sql,
            "status": "ok" if last else "no_sql_result",
            "intent": None,
            "error": None,
            "sql_attempts": len(sql_results),
            "last_failure_stage": stage,
            "tools_used": used,
            "retrieved_ids": retrieved,
            "cited_ids": [],
            "latency_s": round(time.perf_counter() - started, 3),
            "tokens": _tokens(handler),
        }

    return target


def build_e1(
    supervisor_llm: Any, sql_llm: Any, knowledge_llm: Any, runner: SqlRunner, retriever: Retriever
) -> Target:
    app = build_app(
        supervisor_llm=supervisor_llm,
        sql_llm=sql_llm,
        knowledge_llm=knowledge_llm,
        runner=runner,
        retriever=retriever,
        checkpointer=InMemorySaver(),
    )

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        handler = UsageMetadataCallbackHandler()
        try:
            resp = chat(
                app,
                inputs["question"],
                thread_id=f"eval-{uuid.uuid4().hex[:12]}",
                scenario_id=f"sql_gold:{inputs.get('id', '')}",
                callbacks=[handler],
            )
        except ConfigError:
            raise
        except Exception as e:
            return _error_output(e, started)
        ev = next((e for e in resp.evidence if e.get("kind", "sql") == "sql"), None)
        return {
            "answer": resp.answer,
            "final_rows": ev["rows"] if ev else None,
            "sql": ev["sql"] if ev else None,
            "status": resp.status,
            "intent": resp.intent,
            "error": None,
            "sql_attempts": resp.sql_attempts,
            "last_failure_stage": resp.last_failure_stage,
            "tools_used": [],
            "retrieved_ids": [],
            "cited_ids": [c["chunk_id"] for c in resp.citations],
            "latency_s": round(time.perf_counter() - started, 3),
            "tokens": _tokens(handler),
        }

    return target
