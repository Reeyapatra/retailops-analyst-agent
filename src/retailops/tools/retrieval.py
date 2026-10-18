"""search_knowledge tool: retrieval over the knowledge base, output treated as untrusted data."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from retailops.guards.injection import spotlight
from retailops.rag.retriever import Retriever


def make_search_knowledge_tool(retriever: Retriever, seen_ids: list[str] | None = None) -> BaseTool:
    """`seen_ids`, if given, collects the chunk ids returned (used to validate citations)."""

    @tool
    def search_knowledge(query: str) -> str:
        """Search metric definitions, event schema, runbooks and policies. Returns passages
        with ids like `doc_id#heading`; cite those ids. Says so when nothing relevant exists."""
        result = retriever.retrieve(query)
        if result.abstain:
            return spotlight("No relevant passage found in the knowledge base.", "knowledge_base")
        if seen_ids is not None:
            seen_ids.extend(h.chunk.chunk_id for h in result.hits)
        body = "\n\n".join(f"[{h.chunk.chunk_id}]\n{h.chunk.text}" for h in result.hits)
        return spotlight(body, source="knowledge_base")

    return search_knowledge
