---
doc_id: runbook_schema_drift
title: Runbook - schema drift
owner: data-platform (synthetic)
last_reviewed: 2026-10-18
status: draft-pending-author-review
---

# Runbook: schema drift

## Symptoms

A field that is normally always populated suddenly has a high null rate for a few days, and `schema_version` changes from 1 to another value. Downstream reports that filter or group on the field, such as currency, silently drop or mislabel rows.

## Confirm

Look for days where the schema version or the null rate of a key field changes:

```sql
SELECT cast(event_ts AS DATE) AS day, schema_version, count(*) AS events,
       count(*) FILTER (WHERE currency IS NULL) AS null_currency
FROM raw_events
GROUP BY 1, 2 HAVING schema_version <> 1 OR count(*) FILTER (WHERE currency IS NULL) > 0
ORDER BY 1
```

If every event in a window has the new version and the null field, a producer deployed a change without telling consumers. If only a few events are affected, suspect a client bug instead.

## Remediate

Do not guess missing values. Ask the producing team to confirm the schema change and backfill the field, then re-ingest the affected days. Propose a ticket to the data platform and notify the event owner. If the field can be recovered deterministically from a trusted source, the fix still requires approval before any data is changed.

## Escalate

Escalate immediately if the drift is ongoing at the time of the check, since every new event is affected until the producer is fixed.
