---
doc_id: runbook_invalid_payments
title: Runbook - invalid payments
owner: data-platform (synthetic)
last_reviewed: 2026-10-18
status: draft-pending-author-review
---

# Runbook: invalid payments

## Symptoms

Revenue for some days is lower than expected, average order value drops, or `gross_revenue` is zero or negative for paid orders. Payment events must always carry a positive `payment_amount`.

## Confirm

Count payment events with a zero or negative amount, by day:

```sql
SELECT cast(event_ts AS DATE) AS day, count(*) AS invalid_payments,
       sum(payment_amount) AS amount_sum
FROM raw_events
WHERE event_type = 'PAYMENT_COMPLETED' AND payment_amount <= 0
GROUP BY 1 ORDER BY 1
```

Compare against the expected amount, which is quantity times unit price:

```sql
SELECT count(*) AS mismatched_payments
FROM raw_events
WHERE event_type = 'PAYMENT_COMPLETED' AND payment_amount <> quantity * unit_price
```

A cluster of invalid values in a short window suggests a payment-gateway or transformation bug. Scattered single values suggest manual error.

## Remediate

Propose quarantining the invalid payment events so they do not distort revenue, and ask the payments team to supply corrected amounts. Do not edit amounts yourself. Quarantine requires approval under the action policy, and corrected values must come from the source system.

## Escalate

Escalate to finance and the payments team if invalid payments affect more than 1 percent of a day's payment events or more than three days in a row.
