"""Supervisor: structured-output intent routing with a safe deterministic fallback.

The route is a validated Pydantic object, never parsed from free text. If the model's output
fails validation twice, the request goes to the knowledge specialist, which abstains when
unsure (docs/02-ARCHITECTURE.md section 5). Provider or network errors are NOT swallowed.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from retailops.observability import log_event, run_config
from retailops.prompts import load_prompt

Intent = Literal[
    "metric_question",
    "data_quality",
    "anomaly_investigation",
    "knowledge_question",
    "out_of_scope",
]
MAX_ROUTE_ATTEMPTS = 2


class RouteDecision(BaseModel):
    intent: Intent
    standalone_question: str = Field(min_length=1)
    plan: list[str] = Field(default_factory=list, max_length=3)


def route(
    llm: Any,
    user_message: str,
    context: str = "",
    *,
    thread_id: str | None = None,
    scenario_id: str | None = None,
) -> tuple[RouteDecision, bool]:
    """Return (decision, used_fallback)."""
    router = llm.with_structured_output(RouteDecision)
    human = (f"Conversation so far:\n{context}\n\n" if context else "") + (
        f"Latest user message:\n{user_message}"
    )
    messages = [SystemMessage(load_prompt("supervisor_route")), HumanMessage(human)]
    config = run_config("supervisor", thread_id=thread_id, scenario_id=scenario_id)
    for attempt in range(1, MAX_ROUTE_ATTEMPTS + 1):
        try:
            decision = router.invoke(messages, config=config)
            return RouteDecision.model_validate(decision), False
        except (ValidationError, OutputParserException) as e:
            log_event("route_validation_failed", attempt=attempt, error=str(e)[:300])
    log_event("route_fallback", intent="knowledge_question")
    return (
        RouteDecision(
            intent="knowledge_question",
            standalone_question=user_message,
            plan=["Routing failed validation; answer from the knowledge base or abstain."],
        ),
        True,
    )
