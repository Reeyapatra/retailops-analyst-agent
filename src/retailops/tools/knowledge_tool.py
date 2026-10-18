"""ask_knowledge: the Knowledge agent exposed as a tool (agents-as-tools, docs/01 FR-13c).

The caller keeps control of its own workflow: it asks a question, gets a short cited answer or an
abstention, and carries on. A per-request call budget bounds the cost and prevents loops. The
returned text is untrusted data and is spotlighted by the caller before reaching a model.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from retailops.agents.knowledge import answer_knowledge_question
from retailops.rag.retriever import Retriever


class KnowledgeBudgetExceeded(RuntimeError):
    pass


def make_ask_knowledge_tool(
    llm: Any, retriever: Retriever, *, max_calls: int = 2, calls_log: list[str] | None = None
) -> BaseTool:
    """Build a tool bound to one request. `calls_log` records the questions asked."""
    used = {"n": 0}

    @tool
    def ask_knowledge(question: str) -> str:
        """Ask the knowledge assistant for a metric definition, event rule or policy. Returns a
        short answer with source ids, or says the knowledge base does not cover it."""
        if used["n"] >= max_calls:
            raise KnowledgeBudgetExceeded(f"ask_knowledge budget of {max_calls} calls used")
        used["n"] += 1
        if calls_log is not None:
            calls_log.append(question)
        out = answer_knowledge_question(llm, retriever, question)
        if out.status == "abstained":
            return out.answer
        sources = ", ".join(c.chunk_id for c in out.citations)
        return f"{out.answer} [sources: {sources}]"

    return ask_knowledge
