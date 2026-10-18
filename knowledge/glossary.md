---
doc_id: glossary
title: Business glossary
owner: analytics-engineering (synthetic)
last_reviewed: 2026-10-18
status: draft-pending-author-review
---

# Business glossary

## Regions

The business sells in four regions: North, South, East and West. East is the largest by orders, followed by West, North and South. Each region has a synthetic owner handle in `dim_regions`.

## Categories

Products belong to one of six categories: Electronics, Apparel, Home, Beauty, Sports and Grocery. Electronics has the highest prices and therefore the highest revenue per order; Grocery has the lowest.

## Channels

Orders arrive through three channels: `web`, `mobile_app` and `marketplace` (third-party marketplaces).

## Customer segments

Customers are `consumer`, `business` or `vip`. Most orders come from consumers.

## Terms

- **Order:** one customer purchase of one product, possibly several units.
- **Paid order:** an order with a completed payment.
- **Abandoned order:** an order created but never paid.
- **Quarantine:** hiding records from analytics until they are repaired. It is reversible.
- **Backfill:** re-loading corrected historical data.
- **Ingestion lag:** the time between an event happening and the pipeline receiving it.
- **Replay:** the pipeline sending the same events again, which creates duplicates.
- **Baseline:** the normal level of a metric when nothing is wrong.
- **Decoy period:** a time window with ordinary variation and no defect, used to test for false alarms.
