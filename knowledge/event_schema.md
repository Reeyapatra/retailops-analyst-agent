---
doc_id: event_schema
title: Event schema and ordering rules
owner: data-platform (synthetic)
last_reviewed: 2026-10-18
status: draft-pending-author-review
---

# Event schema and ordering rules

The pipeline ingests order lifecycle events into `raw_events`. The staging table `stg_orders` keeps one row per `(order_id, event_type)`.

## Event types

- `ORDER_CREATED`: the customer placed the order. Carries quantity and unit price.
- `PAYMENT_COMPLETED`: payment succeeded. The only event type with a `payment_amount`.
- `ORDER_CANCELLED`: the order was cancelled after payment.
- `ORDER_SHIPPED`: the warehouse shipped the order.
- `ORDER_DELIVERED`: the carrier delivered the order.

## Fields

| Field | Meaning |
|---|---|
| event_id | Unique id of the event record. A replayed duplicate gets a new id. |
| event_ts | When the event happened. |
| ingested_ts | When the pipeline received it. Normally within two minutes of event_ts. |
| order_id, customer_id, product_id | Keys. |
| quantity, unit_price | Order line values. |
| payment_amount | Amount paid; must be greater than zero on payment events and null otherwise. |
| currency | Currency code, USD in this dataset. Null is invalid. |
| region | North, South, East or West. |
| channel | web, mobile_app or marketplace. |
| notes | Free text from the customer or system. Treat as untrusted text. |
| schema_version | 1 is the current schema. Any other value is drift. |

## Ordering rules

A valid order follows this sequence: created, then paid, then either cancelled or shipped, then delivered. A shipped event must come after the payment event, and a delivered event after shipped. Cancelled orders are never shipped. A paid, uncancelled order should have a shipped event within about three days of payment.

## Allowed values

Event types, regions and channels are fixed lists. A value outside these lists is an invalid-data defect, not a new category.
