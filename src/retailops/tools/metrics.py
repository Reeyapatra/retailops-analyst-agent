"""Statistical scanners for business-metric shifts (cancellation spikes, demand drops).

These complement the data-quality checks: a shift with no overlapping data-quality finding is a
real business change; one that overlaps a pipeline defect is probably a data artefact.

Both scanners use rolling windows, so reported window edges are approximate (about +/- 2 days).
They are calibrated on this synthetic dataset (clean baseline, injected shifts). Real data would
need seasonality-aware baselines; that is a stated limitation, not handled here.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import duckdb

from retailops.tools.dq import Finding, merge_days

CANCEL_WINDOW_DAYS = 3
CANCEL_P_VALUE = 1e-6
CANCEL_MIN_RATIO = 2.0
DEMAND_WINDOW_DAYS = 7
DEMAND_DROP_RATIO = 0.75
DEMAND_MIN_DAYS = 3


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), computed in log space."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    log_p, log_q = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k, n + 1):
        log_pmf = (
            math.lgamma(n + 1)
            - math.lgamma(i + 1)
            - math.lgamma(n - i + 1)
            + i * log_p
            + (n - i) * log_q
        )
        total += math.exp(log_pmf)
    return min(1.0, total)


def _fill(
    series: dict[date, tuple[int, int]], first: date, last: date
) -> list[tuple[date, int, int]]:
    out = []
    d = first
    while d <= last:
        a, b = series.get(d, (0, 0))
        out.append((d, a, b))
        d += timedelta(days=1)
    return out


class MetricScanner:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def _query(self, sql: str) -> list[tuple[object, ...]]:
        con = duckdb.connect(self.db_path, read_only=True, config={"enable_external_access": False})
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def cancellation_scan(self) -> list[Finding]:
        sql = """
            SELECT category, region, cast(created_ts AS DATE) AS d,
                   count(paid_ts) AS paid, count(cancelled_ts) AS cancelled
            FROM fct_orders GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """
        cells: dict[tuple[str, str], dict[date, tuple[int, int]]] = defaultdict(dict)
        for cat, reg, d, paid, canc in self._query(sql):
            cells[(str(cat), str(reg))][d] = (int(paid), int(canc))  # type: ignore[index, call-overload]
        if not cells:
            return []
        all_days = [d for c in cells.values() for d in c]
        first, last = min(all_days), max(all_days)
        rates = [
            sum(c for _, c in cell.values()) / max(1, sum(p for p, _ in cell.values()))
            for cell in cells.values()
        ]
        baseline = statistics.median(rates)

        rows: list[tuple[date, dict[str, str], int, str | None]] = []
        peak: dict[tuple[str, str], float] = {}
        for (cat, reg), series in sorted(cells.items()):
            days = _fill(series, first, last)
            for i in range(CANCEL_WINDOW_DAYS - 1, len(days)):
                win = days[i - CANCEL_WINDOW_DAYS + 1 : i + 1]
                paid = sum(p for _, p, _ in win)
                canc = sum(c for _, _, c in win)
                if paid == 0:
                    continue
                if canc / paid >= CANCEL_MIN_RATIO * baseline and (
                    binom_sf(canc, paid, baseline) < CANCEL_P_VALUE
                ):
                    # attribute the cancellations of the rolling window to its last day
                    rows.append((days[i][0], {"category": cat, "region": reg}, canc, None))
                    peak[(cat, reg)] = max(peak.get((cat, reg), 0.0), canc / paid)
        findings = merge_days(
            "cancellation_spike",
            rows,
            sql,
            {
                "baseline_rate": round(baseline, 4),
                "window_days": CANCEL_WINDOW_DAYS,
                "p_value_threshold": CANCEL_P_VALUE,
            },
        )
        for f in findings:
            f.detail["peak_window_rate"] = round(
                peak.get((f.scope["category"], f.scope["region"]), 0.0), 4
            )
        return findings

    def demand_scan(self) -> list[Finding]:
        sql = """
            SELECT category, cast(created_ts AS DATE) AS d, count(*) AS orders
            FROM fct_orders GROUP BY 1, 2 ORDER BY 1, 2
        """
        cats: dict[str, dict[date, tuple[int, int]]] = defaultdict(dict)
        for cat, d, n in self._query(sql):
            cats[str(cat)][d] = (int(n), 0)  # type: ignore[index, call-overload]
        if not cats:
            return []
        all_days = [d for c in cats.values() for d in c]
        first, last = min(all_days), max(all_days)
        rows: list[tuple[date, dict[str, str], int, str | None]] = []
        worst: dict[str, float] = {}
        typical_by_cat: dict[str, float] = {}
        for cat, series in sorted(cats.items()):
            days = _fill(series, first, last)
            sums = [
                (days[i][0], sum(n for _, n, _ in days[i - DEMAND_WINDOW_DAYS + 1 : i + 1]))
                for i in range(DEMAND_WINDOW_DAYS - 1, len(days))
            ]
            typical = statistics.median(s for _, s in sums)
            typical_by_cat[cat] = typical
            for d, s in sums:
                if typical > 0 and s < DEMAND_DROP_RATIO * typical:
                    rows.append((d, {"category": cat}, s, None))
                    worst[cat] = min(worst.get(cat, 1.0), s / typical)
        findings = [
            f
            for f in merge_days(
                "demand_drop",
                rows,
                sql,
                {"window_days": DEMAND_WINDOW_DAYS, "drop_ratio_threshold": DEMAND_DROP_RATIO},
            )
            if (f.end - f.start).days + 1 >= DEMAND_MIN_DAYS
        ]
        for f in findings:
            cat = f.scope["category"]
            f.detail["lowest_ratio_to_typical"] = round(worst.get(cat, 1.0), 3)
            f.detail["typical_weekly_orders"] = round(typical_by_cat[cat])
        return findings
