---
doc_id: data_quality_policy
title: Data quality policy
owner: data-governance (synthetic)
last_reviewed: 2026-10-18
status: draft-pending-author-review
---

# Data quality policy

## Severity levels

- **Sev 1 (critical):** a defect that makes headline metrics wrong right now and is still happening, such as ongoing schema drift or duplicate replays affecting more than 5 percent of a day's events.
- **Sev 2 (major):** a defect confined to a past window or a single region that changed reported metrics, such as a late-arriving feed or a batch of invalid payments.
- **Sev 3 (minor):** a small number of malformed records that do not move any reported metric.

## Response targets

Sev 1 is acknowledged within 1 hour and mitigated the same day. Sev 2 is acknowledged within 1 business day and resolved within 3 business days. Sev 3 is reviewed weekly.

## Who approves quarantine

Quarantining records removes them from analytics until they are repaired. The data steward for the affected domain approves quarantine for Sev 2 and Sev 3. For Sev 1, the on-call data platform lead and the data steward must both approve. The assistant may only propose quarantine; it never performs it without an approved proposal.

## Evidence requirement

Every defect report must state the check that detected it, the number of affected records, the date range and region or category scope, and a sample of identifiers. Reports without counts and scope are returned to the author.

## Real changes are not defects

A drop in demand or a rise in cancellations with clean, complete data is a business signal, not a data quality defect. Do not quarantine data to explain a real business change.
