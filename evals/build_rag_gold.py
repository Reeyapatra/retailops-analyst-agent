"""Write evals/datasets/rag_gold.jsonl (28 answerable + 8 unanswerable knowledge questions).

Gold doc ids refer to knowledge/*.md. Every 4th question is held out from threshold tuning.
Run: uv run python -m evals.build_rag_gold
"""

from __future__ import annotations

import json
from pathlib import Path

DATASET = Path(__file__).parent / "datasets" / "rag_gold.jsonl"

# (question, gold doc ids, reference answer); empty doc list = unanswerable from the knowledge base
ITEMS: list[tuple[str, list[str], str]] = [
    (
        "How is net revenue defined?",
        ["metric_definitions"],
        "Gross revenue excluding cancelled orders; sum(net_revenue) in fct_orders.",
    ),
    (
        "What is the difference between gross and net revenue?",
        ["metric_definitions"],
        "Gross is the recorded payment before cancellation; net excludes cancelled orders.",
    ),
    (
        "How is average order value calculated?",
        ["metric_definitions"],
        "Gross revenue divided by the number of paid orders.",
    ),
    (
        "How do we calculate the cancellation rate?",
        ["metric_definitions"],
        "Cancelled orders divided by paid orders.",
    ),
    (
        "What is a normal baseline cancellation rate?",
        ["metric_definitions", "runbook_cancellation_spike"],
        "About 4 percent of paid orders.",
    ),
    (
        "How is delivery time measured?",
        ["metric_definitions"],
        "Days between the shipped event and the delivered event, averaged over delivered orders.",
    ),
    (
        "Which event types does the pipeline ingest?",
        ["event_schema"],
        "ORDER_CREATED, PAYMENT_COMPLETED, ORDER_CANCELLED, ORDER_SHIPPED, ORDER_DELIVERED.",
    ),
    (
        "In what order should order lifecycle events occur?",
        ["event_schema"],
        "Created, paid, then cancelled or shipped, then delivered.",
    ),
    (
        "What does a schema_version other than 1 mean?",
        ["event_schema", "runbook_schema_drift"],
        "It indicates schema drift.",
    ),
    (
        "How do I confirm that events were duplicated?",
        ["runbook_duplicate_events"],
        "Count records sharing an order and event type; compare raw and staged totals.",
    ),
    (
        "What are the symptoms of a replayed batch of events?",
        ["runbook_duplicate_events"],
        "Raw revenue for a day is about double the deduplicated figure.",
    ),
    (
        "When should duplicate events be escalated?",
        ["runbook_duplicate_events"],
        "More than one day in a week or more than 5 percent of a day's events.",
    ),
    (
        "How do I detect late-arriving events?",
        ["runbook_late_events"],
        "Look for a large gap between ingested_ts and event_ts, grouped by region and day.",
    ),
    (
        "Why does one region's revenue dip and then catch up?",
        ["runbook_late_events"],
        "An ingestion delay for that region's feed; grouped by event date the numbers are correct.",
    ),
    (
        "What should I check before concluding a cancellation spike is an operations problem?",
        ["runbook_cancellation_spike"],
        "That there are no duplicate or late events in the window.",
    ),
    (
        "Who should be notified about a cancellation spike?",
        ["runbook_cancellation_spike"],
        "The category owner and the regional manager.",
    ),
    (
        "How do I find zero or negative payments?",
        ["runbook_invalid_payments"],
        "Query PAYMENT_COMPLETED events with payment_amount <= 0.",
    ),
    (
        "Who supplies corrected payment amounts?",
        ["runbook_invalid_payments"],
        "The payments team, from the source system; the assistant never edits amounts.",
    ),
    (
        "What does a sudden null rate in the currency field indicate?",
        ["runbook_schema_drift"],
        "A producer schema change without notice.",
    ),
    (
        "What are the data quality severity levels?",
        ["data_quality_policy"],
        "Sev 1 critical, Sev 2 major, Sev 3 minor.",
    ),
    (
        "Who approves quarantine for a Sev 1 issue?",
        ["data_quality_policy"],
        "The on-call data platform lead and the data steward.",
    ),
    (
        "What must a defect report include?",
        ["data_quality_policy"],
        "The detecting check, affected count, date range and scope, and sample identifiers.",
    ),
    (
        "Is a drop in demand with clean data a data quality defect?",
        ["data_quality_policy"],
        "No; it is a business signal and data should not be quarantined to explain it.",
    ),
    (
        "What does replay mean in the glossary?",
        ["glossary"],
        "The pipeline sending the same events again, which creates duplicates.",
    ),
    ("Which sales channels exist?", ["glossary"], "web, mobile_app and marketplace."),
    (
        "What actions can the assistant propose?",
        ["action_policy"],
        "quarantine_events, open_ticket and notify_owner.",
    ),
    (
        "Can the assistant execute an action without approval?",
        ["action_policy"],
        "No; every action needs an approved proposal.",
    ),
    (
        "What gets written to the action audit log?",
        ["action_policy"],
        "Proposals, decisions, approver, timestamps, execution result and trace id.",
    ),
    # ---- unanswerable from the knowledge base ----
    ("What is the return policy for customers?", [], ""),
    ("What is the CEO's name?", [], ""),
    ("How do I configure Kubernetes autoscaling?", [], ""),
    ("What is our refund SLA for marketplace orders?", [], ""),
    ("Which carrier ships orders to the South region?", [], ""),
    ("How do I reset my password?", [], ""),
    ("What is the weather forecast for the North region?", [], ""),
    ("Which programming language is the pipeline written in?", [], ""),
]


def main() -> int:
    rows = []
    for i, (question, docs, answer) in enumerate(ITEMS):
        rows.append(
            {
                "id": f"r{i + 1:02d}",
                "question": question,
                "gold_doc_ids": docs,
                "answerable": bool(docs),
                "reference_answer": answer,
                "held_out": i % 4 == 3,
            }
        )
    DATASET.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", "utf-8")
    print(f"wrote {len(rows)} questions to {DATASET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
