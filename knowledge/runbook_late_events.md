---
doc_id: runbook_late_events
title: Runbook - late-arriving events
owner: data-platform (synthetic)
last_reviewed: 2026-10-18
status: draft-pending-author-review
---

# Runbook: late-arriving events

## Symptoms

One region shows an apparent revenue dip for a day or two when metrics are grouped by ingestion date, followed by a catch-up spike. Grouped by event date the numbers are eventually correct. The gap between `event_ts` and `ingested_ts` is far larger than the normal two minutes.

## Confirm

Find regions and days where ingestion lagged:

```sql
SELECT region, cast(event_ts AS DATE) AS day, count(*) AS late_events,
       max(ingested_ts - event_ts) AS max_lag
FROM raw_events
WHERE ingested_ts - event_ts > INTERVAL 1 HOUR
GROUP BY 1, 2 ORDER BY late_events DESC
```

If the lag is limited to one region and a short window, it is an ingestion delay for that region's feed, not a demand change. A real demand drop has no lag between event and ingestion.

## Remediate

No data correction is needed once the events arrive, because staging and `fct_orders` use event time. Tell stakeholders that dashboards built on ingestion time were temporarily wrong for the affected region. Open a ticket with the region's feed owner to find the cause of the delay.

## Escalate

Escalate if lag exceeds 24 hours for more than one region at the same time, or if events have not arrived after 3 days, since they may be lost.
