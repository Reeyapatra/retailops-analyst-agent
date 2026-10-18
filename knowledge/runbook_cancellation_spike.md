---
doc_id: runbook_cancellation_spike
title: Runbook - cancellation spike
owner: operations-analytics (synthetic)
last_reviewed: 2026-10-18
status: draft-pending-author-review
---

# Runbook: cancellation spike

## Symptoms

The overall cancellation rate rises, or one category or region shows a rate several times its baseline. The normal baseline cancellation rate is about 4 percent of paid orders. A spike confined to one category and one region for several days points to a product, supplier or fulfilment problem rather than a pipeline defect.

## Confirm

Compare cancellation rate by category and region over the suspect week against the weeks before it:

```sql
SELECT category, region,
       count(cancelled_ts) * 1.0 / count(paid_ts) AS cancellation_rate,
       count(paid_ts) AS paid_orders
FROM fct_orders
WHERE created_ts >= DATE '2026-04-01' AND created_ts < DATE '2026-04-08'
GROUP BY 1, 2 ORDER BY cancellation_rate DESC
```

Check that the data is sound before blaming operations: confirm there are no duplicate or late events in the same window (see the duplicate events and late events runbooks). A genuine spike has clean data and a concentrated scope.

## Remediate

This is a business issue, not a data defect, so do not quarantine data. Notify the category owner and the regional manager, include the rate, the scope and the date range, and ask whether stock, pricing or a carrier problem explains it. Open a ticket if the rate stays elevated for more than 3 days.

## Escalate

Escalate to operations leadership if the cancelled revenue in the affected scope exceeds 5 percent of weekly revenue for that region.
