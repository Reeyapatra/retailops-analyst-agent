---
doc_id: metric_definitions
title: Metric definitions
owner: analytics-engineering (synthetic)
last_reviewed: 2026-10-18
status: draft-pending-author-review
---

# Metric definitions

All metrics are computed from the warehouse tables. Use `fct_orders` for order-level metrics and `daily_metrics` for fast trend queries. Never compute business metrics from `raw_events`, because it contains duplicate replays and late events.

## Revenue

**Gross revenue** is the recorded payment amount of an order before cancellation: `sum(gross_revenue)` in `fct_orders`. An order that was never paid has gross revenue 0.

**Net revenue** is gross revenue excluding cancelled orders: `sum(net_revenue)`. When someone says "revenue" without a qualifier, report net revenue and say so.

Revenue is assigned to the day the order was **created** (`created_ts`), not the day it was paid or ingested.

## Orders and paid orders

An **order** is one row in `fct_orders`, whether or not it was ever paid. A **paid order** has a non-null `paid_ts`. About 3 percent of orders are abandoned and never paid.

## Average order value (AOV)

AOV is gross revenue divided by the number of paid orders: `sum(gross_revenue) / count(paid_ts)`. The `aov` column in `daily_metrics` uses the same definition per day, region and category. Do not average the daily AOV values; recompute from sums.

## Cancellation rate

Cancellation rate is cancelled orders divided by paid orders: `count(cancelled_ts) * 1.0 / count(paid_ts)`. Only paid orders can be cancelled. A normal baseline is around 4 percent; a sustained rate several times higher in one category or region is a signal worth investigating (see the cancellation spike runbook).

## Delivery time

Delivery time is the number of days between the shipped event and the delivered event, stored as `delivery_days` in `fct_orders`. Average it over delivered orders only. Orders still in transit have a null value.

## Order status

Status is the latest lifecycle state: `cancelled` if a cancellation exists, otherwise `delivered`, `shipped`, `paid` or `created` in that order of precedence.
