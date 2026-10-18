# 03 – Data and Knowledge Base Specification

## 1. Principle
All data is synthetic, seeded and reproducible. Anomalies are injected deliberately and recorded in a ground-truth manifest, so detection and root-cause answers can be scored objectively. This is what makes the evaluation credible.

## 2. Event schema (aligned with the retail event model)
Event types: `ORDER_CREATED`, `PAYMENT_COMPLETED`, `ORDER_CANCELLED`, `ORDER_SHIPPED`, `ORDER_DELIVERED`.

Raw event fields: `event_id` (uuid), `event_type`, `event_ts` (when it happened), `ingested_ts` (when pipeline received it), `order_id`, `customer_id`, `product_id`, `quantity`, `unit_price`, `payment_amount` (nullable except payment events), `currency`, `region`, `channel`, `notes` (free text, used for injection test fixtures), `schema_version`.

## 3. Warehouse tables (DuckDB)
Layered, with simple SQL views (dbt-duckdb is a Tier 3 option).
- `raw_events`: as ingested, including duplicates and late events.
- `stg_orders`: deduplicated per `(order_id, event_type)`, latest `ingested_ts` wins.
- `fct_orders`: one row per order with lifecycle timestamps, status, gross revenue, net revenue (after cancellation), delivery days.
- `dim_products`: product_id, name, category, base_price.
- `dim_customers`: customer_id, segment, signup_date, home_region.
- `dim_regions`: region, country, manager.
- `daily_metrics`: date × region × category aggregates (orders, revenue, cancellations, AOV).
- `dq_check_results`: output table for stored check runs.
- `action_audit_log`: proposals, decisions, actor, timestamp (written by the application database, not the read-only analytics connection).

Catalog: a `schema_catalog.yaml` with plain-English descriptions for every table and column, used by `get_schema` and schema retrieval.

## 4. Generator requirements
- CLI: `python -m retailops.data.generate --seed 42 --days 180 --orders-per-day 800`.
- Realistic seasonality (weekday and holiday effects), category mix, regional mix, price distributions.
- Realistic lifecycle: created → paid → shipped → delivered, with a baseline cancellation rate and delivery time distribution.
- Deterministic given a seed. Output Parquet or load directly into DuckDB file `data/retailops.duckdb`.

## 5. Injected anomalies (ground truth manifest)
Write `data/anomaly_manifest.json` with: `anomaly_id`, `type`, `start`, `end`, `scope` (region/category/product), `magnitude`, `root_cause`, `expected_findings`.

| Type | Injection | Expected evidence |
|---|---|---|
| Duplicate events | Replay a batch of PAYMENT_COMPLETED events for one day | Duplicate counts in raw vs stg; inflated raw revenue |
| Late-arriving events | Delay ingestion of one region's events by 1–2 days | Gap between event_ts and ingested_ts; apparent revenue dip then correction |
| Cancellation spike | Raise cancellations for one category in one region for a week | Cancellation rate delta vs baseline, concentrated scope |
| Revenue drop (real) | Reduce demand for one category for two weeks | Orders and revenue drop with no DQ problem (tests false-alarm resistance) |
| Invalid payments | Negative or zero payment_amount on a subset | DQ check counts |
| Missing shipments | Paid orders with no SHIPPED event after N days | Sequence check counts |
| Schema drift | A few days with `schema_version` 2 where `currency` is null | Null-rate shift |

Include at least two "decoy" periods with no anomaly to measure false positives.

## 6. Injection fixtures (safety)
Seed a small number of rows whose `notes` or product names contain instruction-like text (for example, text telling an assistant to ignore its rules or to run a destructive statement). They are inert test data used to verify the agent treats data as data. Never include real exploit payloads, malware or credentials; keep fixtures simple and clearly fake.

## 7. Knowledge base (`knowledge/*.md`)
Written by hand or drafted by the model then reviewed by the author; every file starts with a front matter block (`doc_id`, `title`, `owner`, `last_reviewed`).
1. `metric_definitions.md`: revenue (gross vs net), AOV, cancellation rate, delivery time; exact formulas and which table to use.
2. `event_schema.md`: fields, event order rules, allowed values.
3. `runbook_duplicate_events.md`: symptoms, confirmation queries, remediation, escalation.
4. `runbook_late_events.md`
5. `runbook_cancellation_spike.md`
6. `runbook_invalid_payments.md`
7. `runbook_schema_drift.md`
8. `data_quality_policy.md`: severity levels, who approves quarantine, SLAs.
9. `glossary.md`: business terms, region names, channel names.
10. `action_policy.md`: which actions exist, who can approve, what is never allowed.

## 8. RAG configuration
- Chunking: split by markdown headings, target 300–500 tokens, 50-token overlap, keep `doc_id` and heading path as metadata.
- Embeddings: local model by default (for example via fastembed) so the demo has no embedding cost; configurable.
- Vector store: Chroma or LanceDB persisted to `data/vectorstore/`.
- Retrieval: top-k 4–6, optional relevance grading step; answers must cite `doc_id#heading`.
- Abstention: if no chunk passes the relevance threshold, say so rather than guess.
- Retrieval evaluation set: 30+ questions each mapped to the gold `doc_id` for hit@k and MRR.
