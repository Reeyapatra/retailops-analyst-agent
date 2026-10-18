"""get_calendar_context: public holidays that may explain demand shifts (docs/01 FR-14).

External API: Nager.Date (https://date.nager.at, no key). Endpoint shape verified by a live call
on 2026-10-18: GET /api/v3/PublicHolidays/{year}/{countryCode} returns a JSON list of objects
with date, localName, name, global, types.

Reliability: timeout, bounded retries with backoff on transport errors / 429 / 5xx, an in-memory
and optional on-disk cache, and a bundled fallback (2026 US federal holidays only) with an
explicit `source` so callers and users can see when live data was unavailable.
Safety: the response is validated field by field, names are length-limited and stripped of
control characters, and the tool output is wrapped as untrusted data. No secrets are involved.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import httpx
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ValidationError

from retailops.guards.injection import looks_like_instruction, spotlight
from retailops.observability import log_event

BASE_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
MAX_RANGE_DAYS = 366
NAME_MAX_CHARS = 80
CACHE_TTL_S = 30 * 24 * 3600

# Fallback used only when the API cannot be reached: US federal holidays for 2026.
FALLBACK_2026_US: tuple[tuple[str, str], ...] = (
    ("2026-01-01", "New Year's Day"),
    ("2026-01-19", "Martin Luther King, Jr. Day"),
    ("2026-02-16", "Presidents Day"),
    ("2026-05-25", "Memorial Day"),
    ("2026-06-19", "Juneteenth"),
    ("2026-07-03", "Independence Day (observed)"),
    ("2026-07-04", "Independence Day"),
    ("2026-09-07", "Labor Day"),
    ("2026-10-12", "Columbus Day"),
    ("2026-11-11", "Veterans Day"),
    ("2026-11-26", "Thanksgiving Day"),
    ("2026-12-25", "Christmas Day"),
)


class Holiday(BaseModel):
    date: date
    name: str
    kind: Literal["public", "observance"]


class CalendarContext(BaseModel):
    source: Literal["api", "cache", "fallback"]
    country: str
    start: date
    end: date
    holidays: list[Holiday]
    note: str = ""


def _clean(name: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", name).strip()
    if looks_like_instruction(text):
        return "[name withheld: instruction-like text]"
    return text[:NAME_MAX_CHARS]


def parse_holidays(payload: object) -> list[Holiday]:
    """Validate an API payload; malformed items are skipped, a malformed payload raises."""
    if not isinstance(payload, list):
        raise ValueError("calendar API did not return a list")
    out: list[Holiday] = []
    for item in payload:
        try:
            if not isinstance(item, dict):
                raise TypeError
            types = item.get("types") or []
            kind = "public" if "Public" in types else "observance"
            out.append(Holiday(date=item["date"], name=_clean(str(item["name"])), kind=kind))
        except (KeyError, TypeError, ValidationError):
            continue
    return out


class CalendarClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        *,
        cache_dir: Path | None = None,
        timeout_s: float = 5.0,
        retries: int = 2,
        backoff_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.http = http or httpx.Client()
        self.cache_dir = cache_dir
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self.sleep = sleep
        self._memory: dict[tuple[int, str], list[Holiday]] = {}
        self.requests_made = 0

    # ---- fetching -----------------------------------------------------------------------

    def _fetch_year(self, year: int, country: str) -> list[Holiday]:
        url = BASE_URL.format(year=year, country=country)
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(self.backoff_s * 2 ** (attempt - 1))
            self.requests_made += 1
            try:
                resp = self.http.get(url, timeout=self.timeout_s)
            except httpx.TransportError as e:
                last = e
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = RuntimeError(f"HTTP {resp.status_code}")
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")  # client error: do not retry
            return parse_holidays(resp.json())
        raise RuntimeError(f"calendar API unavailable after retries: {last}")

    def _cache_path(self, year: int, country: str) -> Path | None:
        return self.cache_dir / f"holidays-{country}-{year}.json" if self.cache_dir else None

    def _load_disk(self, year: int, country: str) -> list[Holiday] | None:
        path = self._cache_path(year, country)
        if path is None or not path.exists():
            return None
        try:
            blob = json.loads(path.read_text("utf-8"))
            if time.time() - float(blob["fetched_at"]) > CACHE_TTL_S:
                return None
            return parse_holidays(blob["holidays"])
        except (OSError, ValueError, KeyError):
            return None

    def _save_disk(self, year: int, country: str, holidays: list[Holiday]) -> None:
        path = self._cache_path(year, country)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            blob = {
                "fetched_at": time.time(),
                "holidays": [h.model_dump(mode="json") for h in holidays],
            }
            path.write_text(json.dumps(blob), encoding="utf-8")
        except OSError:
            pass  # a cache write failure must never break a request

    # ---- public -------------------------------------------------------------------------

    def get_context(self, start: date, end: date, country: str = "US") -> CalendarContext:
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError("country must be a 2-letter ISO code such as 'US'")
        if end < start:
            raise ValueError("end must not be before start")
        if (end - start).days > MAX_RANGE_DAYS:
            raise ValueError(f"date range is limited to {MAX_RANGE_DAYS} days")

        holidays: list[Holiday] = []
        sources: set[str] = set()
        for year in range(start.year, end.year + 1):
            key = (year, country)
            if key in self._memory:
                year_h, src = self._memory[key], "cache"
            elif (disk := self._load_disk(year, country)) is not None:
                self._memory[key] = year_h = disk
                src = "cache"
            else:
                try:
                    year_h = self._fetch_year(year, country)
                    self._memory[key] = year_h
                    self._save_disk(year, country, year_h)
                    src = "api"
                except Exception as e:  # noqa: BLE001 - any failure degrades to the fallback
                    log_event("calendar_fallback", year=year, country=country, error=str(e)[:200])
                    year_h = self._fallback(year, country)
                    src = "fallback"
            sources.add(src)
            holidays += year_h

        selected = sorted((h for h in holidays if start <= h.date <= end), key=lambda h: h.date)
        source: Literal["api", "cache", "fallback"] = (
            "fallback" if "fallback" in sources else ("api" if "api" in sources else "cache")
        )
        note = ""
        if source == "fallback":
            note = (
                "Live holiday data was unavailable; using a bundled 2026 US federal holiday list. "
                "Observances and other years/countries may be missing."
            )
        return CalendarContext(
            source=source, country=country, start=start, end=end, holidays=selected, note=note
        )

    @staticmethod
    def _fallback(year: int, country: str) -> list[Holiday]:
        if year != 2026 or country != "US":
            return []
        return [
            Holiday(date=date.fromisoformat(d), name=n, kind="public") for d, n in FALLBACK_2026_US
        ]


def make_calendar_tool(client: CalendarClient) -> BaseTool:
    @tool
    def get_calendar_context(start_date: str, end_date: str, country: str = "US") -> str:
        """Public holidays between two ISO dates (YYYY-MM-DD) that may explain demand changes.
        Reports whether data came from the live API, a cache, or an offline fallback."""
        try:
            ctx = client.get_context(
                date.fromisoformat(start_date), date.fromisoformat(end_date), country
            )
        except ValueError as e:
            return f"Invalid input: {e}"
        return spotlight(ctx.model_dump_json(), source="calendar_api")

    return get_calendar_context


def window_around(day: date, days: int = 7) -> tuple[date, date]:
    return day - timedelta(days=days), day + timedelta(days=days)
