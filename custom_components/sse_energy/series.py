"""Hourly statistic series from the 15-min profile (pure Python, no Home Assistant imports).

* Hours are keyed by their UTC start, so the doubled 02:00 hour on the autumn DST day
  becomes two separate hours (the portal labels both with the same wall-clock
  `periodFrom`; the second occurrence is detected by the repeated label -> fold=1).
* `cumulate` builds a gap-free hourly `sum` from a known base, so a window of already
  imported hours can be rewritten (revised) without breaking the running total.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .tariff import TariffConfig

HOUR = timedelta(hours=1)

# series suffix -> statistic name; consumption is kWh, cost carries the currency
CONSUMPTION_SERIES = {
    "grid_consumption": "SSE Grid consumption",
    "grid_consumption_vt": "SSE Grid consumption VT",
    "grid_consumption_nt": "SSE Grid consumption NT",
}
COST_SERIES = {
    "grid_cost": "SSE Grid cost",
    "grid_cost_vt": "SSE Grid cost VT",
    "grid_cost_nt": "SSE Grid cost NT",
}


def hourly_slots(day: dict) -> list[tuple[int, int, float]]:
    """Group a day's 15-min slots into (hour, fold, kWh); fold=1 marks a repeated hour."""
    buckets: dict[tuple[int, int], float] = {}
    seen: set[str] = set()
    for slot in (day.get("data") or []):
        label = str(slot.get("periodFrom", "0:00"))
        fold = 1 if label in seen else 0
        seen.add(label)
        hour = int(label.split(":")[0])
        key = (hour, fold)
        buckets[key] = buckets.get(key, 0.0) + float(slot.get("value") or 0.0) / 4.0
    return [(h, f, e) for (h, f), e in sorted(buckets.items())]


def build_series(days: list[dict], tariff: TariffConfig, tz: str) -> dict[str, dict[datetime, float]]:
    """{suffix: {utc_hour_start: value}} for consumption (+ cost when prices are known).

    Cost is produced only when both energy prices and the calibrated distribution rate
    are known (else we'd be inventing a price). Every hour is entirely VT or NT, so the
    VT/NT cost split is exact."""
    tzinfo = ZoneInfo(tz)
    dist = tariff.dist_rate
    can_cost = tariff.price_vt is not None and tariff.price_nt is not None and dist is not None
    names = list(CONSUMPTION_SERIES) + (list(COST_SERIES) if can_cost else [])
    out: dict[str, dict[datetime, float]] = {n: {} for n in names}

    def add(name: str, start: datetime, value: float) -> None:
        bucket = out[name]
        bucket[start] = bucket.get(start, 0.0) + value

    for day in days:
        try:
            y, m, d = (int(x) for x in str(day.get("date")).split("-"))
        except ValueError:
            continue
        for hour, fold, kwh in hourly_slots(day):
            # nonexistent spring-forward hours normalise onto a neighbour and are summed
            start = datetime(y, m, d, hour, tzinfo=tzinfo, fold=fold).astimezone(timezone.utc)
            vt = tariff.is_vt(hour)
            add("grid_consumption", start, kwh)
            add("grid_consumption_vt", start, kwh if vt else 0.0)
            add("grid_consumption_nt", start, 0.0 if vt else kwh)
            if can_cost:
                cost = kwh * ((tariff.price_vt if vt else tariff.price_nt) + dist)
                add("grid_cost", start, cost)
                add("grid_cost_vt", start, cost if vt else 0.0)
                add("grid_cost_nt", start, 0.0 if vt else cost)
    return out


def cumulate(
    points: dict[datetime, float], start: datetime, end: datetime, base_sum: float
) -> list[tuple[datetime, float]]:
    """Running sum for every hour start..end (inclusive, UTC); missing hours count as 0."""
    rows: list[tuple[datetime, float]] = []
    running = base_sum
    t = start
    while t <= end:
        running += points.get(t, 0.0)
        rows.append((t, round(running, 3)))
        t += HOUR
    return rows


def revision_window(
    points: dict[datetime, float],
    last_start: datetime | None,
    revise_from: datetime,
) -> tuple[datetime, datetime] | None:
    """Hours to (re)write: from min(revise_from, first hour after the last stored one),
    never before the data we have, up to max(last data hour, last stored hour)."""
    if not points:
        return None
    first, last = min(points), max(points)
    if last_start is None:  # new series: import everything we have
        start = first
    else:
        start = max(min(revise_from, last_start + HOUR), first)
    end = last if last_start is None else max(last, last_start)
    if start > end:
        return None
    return start, end
