"""Data Quality specialist: choose checks (validated), run them, summarize with evidence.

Two entry points:
- `run_data_quality`: for questions about data health. A model proposes which named checks to
  run; every proposal is validated against the check allowlist and parameter schema, and an
  invalid or empty plan falls back to running all checks (deterministic and safe).
- `run_targeted_checks`: used after a handoff from the SQL Analyst; the checks come from
  deterministic corruption signals, with no model involved in choosing them.
Findings are always produced by SQL templates, never by the model.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from retailops.guards.injection import spotlight
from retailops.observability import log_event, run_config
from retailops.prompts import load_prompt
from retailops.tools.dq import DQ_CHECKS, CheckResult, DqRunner, Finding

MAX_PLANNED_CHECKS = 7
MAX_FINDINGS_IN_PROMPT = 20


class PlannedCheck(BaseModel):
    name: Literal[
        "duplicate_events",
        "late_events",
        "null_fields",
        "invalid_payments",
        "missing_shipments",
        "schema_drift",
        "invalid_sequence",
    ]
    params: dict[str, Any] = Field(default_factory=dict)


class CheckPlan(BaseModel):
    checks: list[PlannedCheck] = Field(default_factory=list, max_length=MAX_PLANNED_CHECKS)


class DQSummary(BaseModel):
    answer: str
    confidence_note: str


class DQOutput(BaseModel):
    status: Literal["ok"] = "ok"
    answer: str
    confidence_note: str
    checks_run: list[str]
    plan_fallback: bool
    findings: list[Finding]


def _run_validated(
    dq: DqRunner, planned: list[PlannedCheck]
) -> tuple[list[CheckResult], list[str]]:
    results: list[CheckResult] = []
    skipped: list[str] = []
    for pc in planned:
        try:
            results.append(dq.run_check(pc.name, **pc.params))
        except ValueError as e:  # includes pydantic validation errors
            skipped.append(f"{pc.name}: {str(e).splitlines()[0][:120]}")
    return results, skipped


def plan_checks(
    llm: Any, dq: DqRunner, question: str, context: str, *, thread_id: str | None
) -> tuple[list[CheckResult], bool, list[str]]:
    """Returns (results, used_fallback, skipped). Provider errors are not swallowed."""
    human = (f"Conversation so far:\n{context}\n\n" if context else "") + f"Question: {question}"
    try:
        plan: CheckPlan = llm.with_structured_output(CheckPlan).invoke(
            [SystemMessage(load_prompt("dq_plan")), HumanMessage(human)],
            config=run_config("data_quality_plan", thread_id=thread_id),
        )
        plan = CheckPlan.model_validate(plan)
    except (ValidationError, OutputParserException) as e:
        log_event("dq_plan_invalid", error=str(e)[:200])
        plan = CheckPlan()
    results, skipped = _run_validated(dq, plan.checks)
    if not results:
        log_event("dq_plan_fallback_all_checks", skipped=skipped)
        return dq.run_all(), True, skipped
    return results, False, skipped


def _findings_blob(results: list[CheckResult]) -> str:
    findings = [f for r in results for f in r.findings][:MAX_FINDINGS_IN_PROMPT]
    passed = [r.check_name for r in results if r.status == "pass"]
    return json.dumps(
        {
            "checks_run": [r.check_name for r in results],
            "checks_with_no_findings": passed,
            "findings": [f.model_dump(mode="json", exclude={"sql"}) for f in findings],
        }
    )


def run_data_quality(
    llm: Any, dq: DqRunner, question: str, context: str = "", *, thread_id: str | None = None
) -> DQOutput:
    results, fallback, skipped = plan_checks(llm, dq, question, context, thread_id=thread_id)
    findings = [f for r in results for f in r.findings]
    human = f"Question: {question}\n\n" + spotlight(_findings_blob(results), source="dq_checks")
    if skipped:
        human += "\n\nSome planned checks were rejected: " + "; ".join(skipped)
    summary: DQSummary = llm.with_structured_output(DQSummary).invoke(
        [SystemMessage(load_prompt("dq_summarize")), HumanMessage(human)],
        config=run_config("data_quality_summary", thread_id=thread_id),
    )
    return DQOutput(
        answer=summary.answer,
        confidence_note=summary.confidence_note,
        checks_run=[r.check_name for r in results],
        plan_fallback=fallback,
        findings=findings,
    )


def run_targeted_checks(
    dq: DqRunner, check_names: list[str], reasons: list[str]
) -> tuple[list[Finding], str]:
    """Deterministic follow-up after a handoff. Returns findings and a plain-text note."""
    results = [dq.run_check(n) for n in check_names if n in DQ_CHECKS]
    findings = [f for r in results for f in r.findings]
    if not findings:
        note = (
            "Data quality note: the result looked suspicious ("
            + "; ".join(reasons)
            + ") but the follow-up checks "
            + ", ".join(r.check_name for r in results)
            + " found no matching defect."
        )
    else:
        lines = [
            f"- {f.kind}: {f.start} to {f.end}, scope {f.scope or 'all'}, {f.count} affected "
            f"(sample id {f.sample_ids[0] if f.sample_ids else 'n/a'})"
            for f in findings[:5]
        ]
        note = (
            "Data quality note: the result looked suspicious ("
            + "; ".join(reasons)
            + "), and follow-up checks confirm a defect that may affect this figure:\n"
            + "\n".join(lines)
            + "\nConsult the matching runbook before relying on these numbers."
        )
    return findings, note
