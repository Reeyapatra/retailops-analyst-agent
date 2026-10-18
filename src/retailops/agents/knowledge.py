"""Knowledge agent: retrieve -> (abstain | answer with citations) -> validate citations.

Abstention happens in two places: before the model (nothing passed the relevance threshold, so
the model is never asked and cannot invent an answer), and by the model (`answerable=false`).
Citations the model returns must be ids of passages that were actually retrieved; anything else
is dropped, and an answer left with no valid citation is converted into an abstention.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from retailops.guards.injection import looks_like_instruction, spotlight
from retailops.observability import log_event, run_config
from retailops.prompts import load_prompt
from retailops.rag.retriever import Retriever

ABSTAIN_ANSWER = "I don't know: the knowledge base does not cover this."


class KnowledgeDraft(BaseModel):
    answerable: bool
    answer: str
    citations: list[str] = []


class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    heading: str


class KnowledgeOutput(BaseModel):
    status: Literal["answered", "abstained"]
    answer: str
    citations: list[Citation]
    confidence_note: str
    retrieved_ids: list[str]
    top_score: float


def _abstain(reason: str, retrieved: list[str], top: float) -> KnowledgeOutput:
    return KnowledgeOutput(
        status="abstained",
        answer=ABSTAIN_ANSWER,
        citations=[],
        confidence_note=reason,
        retrieved_ids=retrieved,
        top_score=top,
    )


def answer_knowledge_question(
    llm: Any,
    retriever: Retriever,
    question: str,
    *,
    context: str = "",
    scenario_id: str | None = None,
    thread_id: str | None = None,
) -> KnowledgeOutput:
    result = retriever.retrieve(question)
    retrieved = [h.chunk.chunk_id for h in result.hits]
    if result.abstain:
        log_event("knowledge_abstain", stage="retrieval", top_score=result.top_score)
        return _abstain(
            "No passage was relevant enough to the question.", retrieved, result.top_score
        )

    flagged = [h.chunk.chunk_id for h in result.hits if looks_like_instruction(h.chunk.text)]
    if flagged:
        log_event("instruction_like_text_in_passage", chunk_ids=flagged)

    passages = "\n\n".join(f"[{h.chunk.chunk_id}]\n{h.chunk.text}" for h in result.hits)
    human = f"Question: {question}\n\n" + spotlight(passages, source="knowledge_base")
    if context:
        human = f"Conversation context:\n{context}\n\n" + human
    draft: KnowledgeDraft = llm.with_structured_output(KnowledgeDraft).invoke(
        [SystemMessage(load_prompt("knowledge_answer")), HumanMessage(human)],
        config=run_config("knowledge", thread_id=thread_id, scenario_id=scenario_id),
    )

    if not draft.answerable:
        return _abstain(
            "The retrieved passages did not contain the answer.", retrieved, result.top_score
        )

    by_id = {h.chunk.chunk_id: h.chunk for h in result.hits}
    valid = [c for c in dict.fromkeys(draft.citations) if c in by_id]
    if len(valid) < len(set(draft.citations)):
        log_event("invalid_citations_dropped", given=draft.citations, kept=valid)
    if not valid:
        return _abstain(
            "The answer cited no retrieved passage, so it was not trusted.",
            retrieved,
            result.top_score,
        )

    citations = [
        Citation(
            chunk_id=cid,
            doc_id=by_id[cid].doc_id,
            title=by_id[cid].title,
            heading=" > ".join(by_id[cid].heading_path),
        )
        for cid in valid
    ]
    return KnowledgeOutput(
        status="answered",
        answer=draft.answer,
        citations=citations,
        confidence_note=f"Based on {len(citations)} cited passage(s); top relevance "
        f"{result.top_score:.2f}.",
        retrieved_ids=retrieved,
        top_score=result.top_score,
    )
