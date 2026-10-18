"""Build evals/datasets/sql_gold.jsonl: questions, gold SQL, and gold results from the warehouse.

Gold results are produced by *executing the gold SQL* on the default dataset (seed 42, 180 days,
800 orders/day), so they are only valid for that data configuration (recorded in each row).
Run `make data` first, then: uv run python -m evals.build_gold
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from retailops.config import get_settings
from retailops.tools.sql import SqlRunner

DATASET = Path(__file__).parent / "datasets" / "sql_gold.jsonl"
DATA_CONFIG = {"seed": 42, "days": 180, "orders_per_day": 800}

# (id, difficulty, question, gold_sql, order_matters)
QUESTIONS: list[tuple[str, str, str, str, bool]] = [
    # ---- easy ----
    (
        "g01",
        "easy",
        "How many orders were created in total?",
        "SELECT count(*) AS orders FROM fct_orders",
        False,
    ),
    (
        "g02",
        "easy",
        "What is the total revenue?",
        "SELECT sum(net_revenue) AS net_revenue FROM fct_orders",
        False,
    ),
    (
        "g03",
        "easy",
        "How many orders were cancelled?",
        "SELECT count(*) AS cancelled_orders FROM fct_orders WHERE cancelled_ts IS NOT NULL",
        False,
    ),
    (
        "g04",
        "easy",
        "How many distinct customers placed an order?",
        "SELECT count(DISTINCT customer_id) AS customers FROM fct_orders",
        False,
    ),
    (
        "g05",
        "easy",
        "What is the average order value?",
        "SELECT sum(gross_revenue) / count(paid_ts) AS aov FROM fct_orders",
        False,
    ),
    (
        "g06",
        "easy",
        "How many orders were created in the West region?",
        "SELECT count(*) AS orders FROM fct_orders WHERE region = 'West'",
        False,
    ),
    (
        "g07",
        "easy",
        "How many products are in the Electronics category?",
        "SELECT count(*) AS products FROM dim_products WHERE category = 'Electronics'",
        False,
    ),
    (
        "g08",
        "easy",
        "What was the revenue in March 2026?",
        "SELECT sum(net_revenue) AS net_revenue FROM fct_orders "
        "WHERE created_ts >= DATE '2026-03-01' AND created_ts < DATE '2026-04-01'",
        False,
    ),
    (
        "g09",
        "easy",
        "How many orders were created on 2026-02-14?",
        "SELECT count(*) AS orders FROM fct_orders "
        "WHERE created_ts >= DATE '2026-02-14' AND created_ts < DATE '2026-02-15'",
        False,
    ),
    (
        "g10",
        "easy",
        "How many orders have been delivered?",
        "SELECT count(*) AS delivered_orders FROM fct_orders WHERE delivered_ts IS NOT NULL",
        False,
    ),
    # ---- medium ----
    (
        "g11",
        "medium",
        "Show revenue by region, highest first.",
        "SELECT region, sum(net_revenue) AS net_revenue FROM fct_orders "
        "GROUP BY region ORDER BY net_revenue DESC",
        True,
    ),
    (
        "g12",
        "medium",
        "What are the top 5 products by revenue? Show product name and revenue.",
        "SELECT p.name, sum(f.net_revenue) AS net_revenue FROM fct_orders f "
        "JOIN dim_products p USING (product_id) GROUP BY p.name "
        "ORDER BY net_revenue DESC, p.name LIMIT 5",
        True,
    ),
    (
        "g13",
        "medium",
        "What is the cancellation rate by category, highest first?",
        "SELECT category, count(cancelled_ts) * 1.0 / count(paid_ts) AS cancellation_rate "
        "FROM fct_orders GROUP BY category ORDER BY cancellation_rate DESC",
        True,
    ),
    (
        "g14",
        "medium",
        "Show monthly revenue for each month of 2026, in calendar order.",
        "SELECT date_trunc('month', created_ts)::DATE AS month, sum(net_revenue) AS net_revenue "
        "FROM fct_orders GROUP BY 1 ORDER BY 1",
        True,
    ),
    (
        "g15",
        "medium",
        "What is the average delivery time in days by region?",
        "SELECT region, avg(delivery_days) AS avg_delivery_days FROM fct_orders "
        "WHERE delivered_ts IS NOT NULL GROUP BY region ORDER BY region",
        False,
    ),
    (
        "g16",
        "medium",
        "How many orders came from each sales channel?",
        "SELECT channel, count(*) AS orders FROM fct_orders GROUP BY channel",
        False,
    ),
    (
        "g17",
        "medium",
        "Which region had the highest cancellation rate in April 2026?",
        "SELECT region FROM fct_orders WHERE created_ts >= DATE '2026-04-01' "
        "AND created_ts < DATE '2026-05-01' GROUP BY region "
        "ORDER BY count(cancelled_ts) * 1.0 / count(paid_ts) DESC LIMIT 1",
        True,
    ),
    (
        "g18",
        "medium",
        "How many orders did each customer segment place?",
        "SELECT c.segment, count(*) AS orders FROM fct_orders f "
        "JOIN dim_customers c USING (customer_id) GROUP BY c.segment",
        False,
    ),
    (
        "g19",
        "medium",
        "What is the average order value by category?",
        "SELECT category, sum(gross_revenue) / count(paid_ts) AS aov FROM fct_orders "
        "GROUP BY category",
        False,
    ),
    (
        "g20",
        "medium",
        "How many paid, non-cancelled orders have no shipment and were paid before June 1, 2026?",
        "SELECT count(*) AS unshipped_orders FROM fct_orders WHERE paid_ts IS NOT NULL "
        "AND cancelled_ts IS NULL AND shipped_ts IS NULL AND paid_ts < DATE '2026-06-01'",
        False,
    ),
    (
        "g21",
        "medium",
        "Compare the number of Apparel orders in April 2026 and May 2026.",
        "SELECT date_trunc('month', created_ts)::DATE AS month, count(*) AS orders "
        "FROM fct_orders WHERE category = 'Apparel' AND created_ts >= DATE '2026-04-01' "
        "AND created_ts < DATE '2026-06-01' GROUP BY 1 ORDER BY 1",
        True,
    ),
    (
        "g22",
        "medium",
        "Which 3 days had the most orders created? Show the date and count.",
        "SELECT created_ts::DATE AS day, count(*) AS orders FROM fct_orders "
        "GROUP BY 1 ORDER BY orders DESC, day LIMIT 3",
        True,
    ),
    (
        "g23",
        "medium",
        "What was the cancellation rate for Electronics orders in the South region "
        "from April 1 to April 7, 2026?",
        "SELECT count(cancelled_ts) * 1.0 / count(paid_ts) AS cancellation_rate FROM fct_orders "
        "WHERE category = 'Electronics' AND region = 'South' "
        "AND created_ts >= DATE '2026-04-01' AND created_ts < DATE '2026-04-08'",
        False,
    ),
    # ---- hard ----
    (
        "g24",
        "hard",
        "For each region, what was the percent change in revenue from February to March 2026?",
        "WITH m AS (SELECT region, date_trunc('month', created_ts)::DATE AS month, "
        "sum(net_revenue) AS rev FROM fct_orders WHERE created_ts >= DATE '2026-02-01' "
        "AND created_ts < DATE '2026-04-01' GROUP BY 1, 2) "
        "SELECT a.region, (b.rev - a.rev) / a.rev * 100 AS pct_change FROM m a "
        "JOIN m b ON a.region = b.region AND a.month = DATE '2026-02-01' "
        "AND b.month = DATE '2026-03-01' ORDER BY a.region",
        False,
    ),
    (
        "g25",
        "hard",
        "Show the running total of revenue by month.",
        "SELECT month, sum(rev) OVER (ORDER BY month) AS cumulative_revenue FROM "
        "(SELECT date_trunc('month', created_ts)::DATE AS month, sum(net_revenue) AS rev "
        "FROM fct_orders GROUP BY 1) ORDER BY month",
        True,
    ),
    (
        "g26",
        "hard",
        "For each region, which category had the highest revenue?",
        "SELECT region, category FROM (SELECT region, category, "
        "rank() OVER (PARTITION BY region ORDER BY sum(net_revenue) DESC) AS rnk "
        "FROM fct_orders GROUP BY region, category) WHERE rnk = 1 ORDER BY region",
        False,
    ),
    (
        "g27",
        "hard",
        "Rank the top 3 Electronics products by revenue, with name and revenue.",
        "SELECT p.name, sum(f.net_revenue) AS net_revenue FROM fct_orders f "
        "JOIN dim_products p USING (product_id) WHERE f.category = 'Electronics' "
        "GROUP BY p.name ORDER BY net_revenue DESC, p.name LIMIT 3",
        True,
    ),
    (
        "g28",
        "hard",
        "What percentage of paid orders were paid within 10 minutes of creation?",
        "SELECT count(*) FILTER (WHERE paid_ts - created_ts <= INTERVAL 10 MINUTE) * 100.0 "
        "/ count(paid_ts) AS pct_paid_within_10_min FROM fct_orders WHERE paid_ts IS NOT NULL",
        False,
    ),
    (
        "g29",
        "hard",
        "How many duplicate payment events are there in the raw event stream "
        "compared with the deduplicated staging table?",
        "SELECT (SELECT count(*) FROM raw_events WHERE event_type = 'PAYMENT_COMPLETED') - "
        "(SELECT count(*) FROM stg_orders WHERE event_type = 'PAYMENT_COMPLETED') "
        "AS duplicate_payment_events",
        False,
    ),
    (
        "g30",
        "hard",
        "For each month, what share of that month's revenue came from the Electronics category?",
        "SELECT date_trunc('month', created_ts)::DATE AS month, "
        "sum(net_revenue) FILTER (WHERE category = 'Electronics') * 1.0 / sum(net_revenue) "
        "AS electronics_share FROM fct_orders GROUP BY 1 ORDER BY 1",
        True,
    ),
    (
        "g31",
        "hard",
        "For each region, how many orders were created on its busiest day?",
        "SELECT region, max(n) AS max_daily_orders FROM (SELECT region, created_ts::DATE AS d, "
        "count(*) AS n FROM fct_orders GROUP BY 1, 2) GROUP BY region ORDER BY region",
        False,
    ),
]


def _round(v: object) -> object:
    return round(v, 4) if isinstance(v, float) else v


def build(db_path: str) -> list[dict[str, object]]:
    runner = SqlRunner(db_path)
    rows_out: list[dict[str, object]] = []
    for i, (qid, difficulty, question, sql, order_matters) in enumerate(QUESTIONS):
        res = runner.run(sql)
        if not res.ok or res.row_count == 0 or res.truncated:
            raise SystemExit(f"{qid}: gold SQL unusable: {res.error_code} {res.error_message}")
        rows_out.append(
            {
                "id": qid,
                "difficulty": difficulty,
                "question": question,
                "gold_sql": sql,
                "order_matters": order_matters,
                "gold_columns": res.columns,
                "gold_rows": [[_round(v) for v in r] for r in res.rows],
                "data_config": DATA_CONFIG,
                # every 5th question is held out from prompt tuning (docs/04 section 2)
                "held_out": i % 5 == 4,
            }
        )
    return rows_out


def main() -> int:
    db_path = get_settings().duckdb_path
    if not Path(db_path).exists():
        print(f"{db_path} not found; run `make data` first", file=sys.stderr)
        return 1
    rows = build(db_path)
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", "utf-8")
    print(f"wrote {len(rows)} questions to {DATASET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
