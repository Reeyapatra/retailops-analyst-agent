---
doc_id: runbook_duplicate_events
title: Runbook - duplicate events
owner: data-platform (synthetic)
last_reviewed: 2026-10-18
status: draft-pending-author-review
---

# Runbook: duplicate events

## Symptoms

Raw revenue for a day is far higher than the deduplicated figure, often close to double. Order counts in `fct_orders` look normal because staging removes duplicates, but anything computed directly from `raw_events` is inflated. A replayed batch usually affects one event type on one day.

## Confirm

Count event records that share an order and event type:

```sql
SELECT event_type, cast(event_ts AS DATE) AS day, count(*) AS duplicate_records
FROM (
  SELECT order_id, event_type, min(event_ts) AS event_ts, count(*) AS n
  FROM raw_events GROUP BY order_id, event_type HAVING count(*) > 1
)
GROUP BY 1, 2 ORDER BY duplicate_records DESC
```

Compare raw and staged payment totals for the suspect day:

```sql
SELECT
  (SELECT sum(payment_amount) FROM raw_events WHERE event_type = 'PAYMENT_COMPLETED') AS raw_total,
  (SELECT sum(payment_amount) FROM stg_orders WHERE event_type = 'PAYMENT_COMPLETED') AS staged_total
```

Duplicates that arrive hours after the original, with new event ids but the same order, indicate a pipeline replay rather than customer behaviour.

## Remediate

Staging already keeps the latest record per order and event type, so business metrics in `fct_orders` are safe. Propose quarantining the duplicate records in `raw_events` and open a ticket for the owning pipeline team to fix the replay source. Quarantine requires approval under the action policy.

## Escalate

Escalate to the data platform on-call if duplicates appear on more than one day in a week, or if more than 5 percent of a day's events are affected.
