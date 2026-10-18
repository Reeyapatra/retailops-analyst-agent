"""Named, parameter-validated data-quality checks over the warehouse (docs/02 tool `run_dq_check`).

Each check is a vetted SQL template; the caller supplies only validated parameters (dates, a
region, thresholds, and a column from a fixed allowlist), never SQL. A check returns structured
findings: kind, date window, scope, affected count, sample ids and the SQL that produced them.
Consecutive days with the same scope are merged into one finding; a single missing day (gap of
at most 2 days between hits) does not split a window.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field

DQ_CHECKS = (
    "duplicate_events",
    "late_events",
    "null_fields",
    "invalid_payments",
    "missing_shipments",
    "schema_drift",
    "invalid_sequence",
)
Region = Literal["North", "South", "East", "West"]
NullableColumn = Literal["currency", "customer_id", "product_id", "region", "channel"]
ALL_NULLABLE: list[NullableColumn] = ["currency", "customer_id", "product_id", "region", "channel"]
MAX_GAP_DAYS = 2

FindingKind = Literal[
    "duplicate_events",
    "late_events",
    "null_fields",
    "invalid_payments",
    "missing_shipments",
    "schema_drift",
    "invalid_sequence",
    "cancellation_spike",
    "demand_drop",
]


class CheckParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date | None = None
    end: date | None = None
    region: Region | None = None
    min_lag_minutes: int = Field(60, ge=1, le=10080)
    min_days_unshipped: int = Field(7, ge=1, le=60)
    columns: list[NullableColumn] = Field(default_factory=lambda: list(ALL_NULLABLE))


class Finding(BaseModel):
    finding_id: str
    kind: FindingKind
    start: date
    end: date
    scope: dict[str, str]
    count: int
    detail: dict[str, Any] = {}
    sample_ids: list[str] = []
    sql: str = ""


class CheckResult(BaseModel):
    check_name: str
    params: dict[str, Any]
    status: Literal["pass", "fail"]
    finding_count: int
    findings: list[Finding]


def merge_days(
    kind: FindingKind,
    rows: Iterable[tuple[date, dict[str, str], int, str | None]],
    sql: str,
    detail: dict[str, Any] | None = None,
) -> list[Finding]:
    """Merge per-day rows into windows per scope."""
    by_scope: dict[tuple[tuple[str, str], ...], list[tuple[date, int, str | None]]] = {}
    for day, scope, count, sample in rows:
        by_scope.setdefault(tuple(sorted(scope.items())), []).append((day, count, sample))
    findings: list[Finding] = []
    for scope_key, items in sorted(by_scope.items()):
        items.sort()
        window: list[tuple[date, int, str | None]] = []
        for item in [*items, None]:
            if window and (item is None or (item[0] - window[-1][0]).days > MAX_GAP_DAYS):
                start, end = window[0][0], window[-1][0]
                scope = dict(scope_key)
                findings.append(
                    Finding(
                        finding_id=f"{kind}:{start}:{end}:{'|'.join(scope.values()) or 'all'}",
                        kind=kind,
                        start=start,
                        end=end,
                        scope=scope,
                        count=sum(c for _, c, _ in window),
                        detail=dict(detail or {}),
                        sample_ids=[s for _, _, s in window if s][:3],
                        sql=" ".join(sql.split()),
                    )
                )
                window = []
            if item is not None:
                window.append(item)
    return sorted(findings, key=lambda f: (f.start, f.finding_id))


class DqRunner:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def _query(self, sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
        con = duckdb.connect(self.db_path, read_only=True, config={"enable_external_access": False})
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def run_all(self, **params: Any) -> list[CheckResult]:
        return [self.run_check(name, **params) for name in DQ_CHECKS]

    def run_check(self, check_name: str, **params: Any) -> CheckResult:
        if check_name not in DQ_CHECKS:
            raise ValueError(f"unknown check '{check_name}'; allowed: {list(DQ_CHECKS)}")
        p = CheckParams(**params)
        findings = getattr(self, f"_check_{check_name}")(p)
        return CheckResult(
            check_name=check_name,
            params=p.model_dump(mode="json", exclude_defaults=True),
            status="fail" if findings else "pass",
            finding_count=len(findings),
            findings=findings,
        )

    # ---- helpers -------------------------------------------------------------------------

    @staticmethod
    def _range(col: str) -> str:
        return (
            f"cast({col} AS DATE) BETWEEN coalesce(?, DATE '1900-01-01') "
            "AND coalesce(?, DATE '2999-12-31')"
        )

    @staticmethod
    def _bounds(p: CheckParams) -> list[Any]:
        return [p.start, p.end]

    @staticmethod
    def _region(p: CheckParams) -> list[Any]:
        return [p.region, p.region]

    # ---- checks --------------------------------------------------------------------------

    def _check_duplicate_events(self, p: CheckParams) -> list[Finding]:
        sql = f"""
            SELECT cast(first_ts AS DATE) AS day, event_type, sum(n - 1) AS extra,
                   min(order_id) AS sample
            FROM (SELECT order_id, event_type, min(event_ts) AS first_ts, count(*) AS n,
                         max(region) AS region
                  FROM raw_events GROUP BY 1, 2 HAVING count(*) > 1)
            WHERE {self._range("first_ts")} AND (? IS NULL OR region = ?)
            GROUP BY 1, 2 ORDER BY 1
        """
        rows = self._query(sql, [*self._bounds(p), *self._region(p)])
        return merge_days(
            "duplicate_events",
            ((r[0], {"event_type": r[1]}, int(r[2]), r[3]) for r in rows),
            sql,
        )

    def _check_late_events(self, p: CheckParams) -> list[Finding]:
        sql = f"""
            SELECT cast(event_ts AS DATE) AS day, region, count(*) AS n, min(order_id) AS sample
            FROM (SELECT *, row_number() OVER (
                    PARTITION BY order_id, event_type ORDER BY ingested_ts) AS rn
                  FROM raw_events)
            WHERE rn = 1 AND epoch(ingested_ts) - epoch(event_ts) > ? * 60
              AND {self._range("event_ts")} AND (? IS NULL OR region = ?)
            GROUP BY 1, 2 ORDER BY 1
        """
        rows = self._query(sql, [p.min_lag_minutes, *self._bounds(p), *self._region(p)])
        return merge_days(
            "late_events",
            ((r[0], {"region": r[1]}, int(r[2]), r[3]) for r in rows),
            sql,
            {"min_lag_minutes": p.min_lag_minutes},
        )

    def _check_null_fields(self, p: CheckParams) -> list[Finding]:
        findings: list[Finding] = []
        for col in p.columns:  # column names come from a Literal allowlist
            sql = f"""
                SELECT cast(event_ts AS DATE) AS day, count(*) AS n, min(order_id) AS sample
                FROM raw_events
                WHERE {col} IS NULL AND {self._range("event_ts")} AND (? IS NULL OR region = ?)
                GROUP BY 1 ORDER BY 1
            """
            rows = self._query(sql, [*self._bounds(p), *self._region(p)])
            findings += merge_days(
                "null_fields", ((r[0], {"field": col}, int(r[1]), r[2]) for r in rows), sql
            )
        return sorted(findings, key=lambda f: (f.start, f.finding_id))

    def _check_invalid_payments(self, p: CheckParams) -> list[Finding]:
        sql = f"""
            SELECT cast(event_ts AS DATE) AS day, count(*) AS n, min(order_id) AS sample
            FROM raw_events
            WHERE event_type = 'PAYMENT_COMPLETED'
              AND (payment_amount IS NULL OR payment_amount <= 0)
              AND {self._range("event_ts")} AND (? IS NULL OR region = ?)
            GROUP BY 1 ORDER BY 1
        """
        rows = self._query(sql, [*self._bounds(p), *self._region(p)])
        return merge_days(
            "invalid_payments",
            ((r[0], {"event_type": "PAYMENT_COMPLETED"}, int(r[1]), r[2]) for r in rows),
            sql,
        )

    def _check_missing_shipments(self, p: CheckParams) -> list[Finding]:
        sql = f"""
            WITH latest AS (SELECT max(event_ts) AS t FROM raw_events)
            SELECT cast(paid_ts AS DATE) AS day, region, count(*) AS n, min(order_id) AS sample
            FROM fct_orders, latest
            WHERE paid_ts IS NOT NULL AND cancelled_ts IS NULL AND shipped_ts IS NULL
              AND paid_ts < latest.t - to_days(cast(? AS INTEGER))
              AND {self._range("paid_ts")} AND (? IS NULL OR region = ?)
            GROUP BY 1, 2 ORDER BY 1
        """
        rows = self._query(sql, [p.min_days_unshipped, *self._bounds(p), *self._region(p)])
        return merge_days(
            "missing_shipments",
            ((r[0], {"region": r[1]}, int(r[2]), r[3]) for r in rows),
            sql,
            {"min_days_unshipped": p.min_days_unshipped},
        )

    def _check_schema_drift(self, p: CheckParams) -> list[Finding]:
        sql = f"""
            SELECT cast(event_ts AS DATE) AS day, count(*) AS n, min(order_id) AS sample,
                   string_agg(DISTINCT cast(schema_version AS VARCHAR), ',') AS versions
            FROM raw_events
            WHERE schema_version <> 1 AND {self._range("event_ts")}
              AND (? IS NULL OR region = ?)
            GROUP BY 1 ORDER BY 1
        """
        rows = self._query(sql, [*self._bounds(p), *self._region(p)])
        versions = sorted({v for r in rows for v in str(r[3]).split(",")})
        return merge_days(
            "schema_drift",
            ((r[0], {"field": "schema_version"}, int(r[1]), r[2]) for r in rows),
            sql,
            {"versions_seen": versions},
        )

    def _check_invalid_sequence(self, p: CheckParams) -> list[Finding]:
        sql = f"""
            SELECT cast(created_ts AS DATE) AS day, region, count(*) AS n, min(order_id) AS sample
            FROM fct_orders
            WHERE (shipped_ts < paid_ts OR delivered_ts < shipped_ts
                   OR (cancelled_ts IS NOT NULL AND shipped_ts IS NOT NULL)
                   OR paid_ts < created_ts)
              AND {self._range("created_ts")} AND (? IS NULL OR region = ?)
            GROUP BY 1, 2 ORDER BY 1
        """
        rows = self._query(sql, [*self._bounds(p), *self._region(p)])
        return merge_days(
            "invalid_sequence",
            ((r[0], {"region": r[1]}, int(r[2]), r[3]) for r in rows),
            sql,
        )


DQ_KINDS: frozenset[str] = frozenset(DQ_CHECKS)


def overlap(a: Finding, b: Finding) -> bool:
    return a.start <= b.end and b.start <= a.end


def classify_cause(finding: Finding, dq_findings: Iterable[Finding]) -> str:
    """'data_quality' if the finding is itself a pipeline defect, or a metric shift whose window
    overlaps one; otherwise 'business_change' (clean data, so the shift is probably real)."""
    if finding.kind in DQ_KINDS:
        return "data_quality"
    return (
        "data_quality"
        if any(f.kind in DQ_KINDS and overlap(finding, f) for f in dq_findings)
        else "business_change"
    )
