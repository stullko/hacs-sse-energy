"""Import accurate hourly long-term statistics into Home Assistant.

External statistics (statistic_id contains ':') carry historical timestamps, so
the Energy dashboard shows correct hourly/daily bars despite SSE's 1-2 day delay.
This is the same pattern utility integrations like `opower` use.

Forward-only: only hours strictly after the recorded last `sum` are added, so the
cumulative sum stays monotonic. Re-running is safe (recorder upserts by id+start).
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .tariff import TariffConfig, hourly_energy

_LOGGER = logging.getLogger(__name__)

try:  # mean_type/unit_class are required from HA Core 2026.11 (added 2025.11)
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_NONE = StatisticMeanType.NONE
    _NEW_META = True
except ImportError:  # pragma: no cover - older HA
    _MEAN_NONE = None
    _NEW_META = False


def _series(days: list[dict], tariff: TariffConfig, tz: str):
    """Return [(statistic_id, name, unit, unit_class, points[(dt, value)]), ...].

    Consumption series are always produced; the cost series is added only when the
    live API supplied both energy prices and a calibrated distribution rate (else
    we'd be inventing a price - we don't)."""
    tzinfo = ZoneInfo(tz)
    cons: list[tuple[datetime, float]] = []
    vt: list[tuple[datetime, float]] = []
    nt: list[tuple[datetime, float]] = []
    cost: list[tuple[datetime, float]] = []

    dist = tariff.dist_rate
    can_cost = (
        tariff.price_vt is not None and tariff.price_nt is not None and dist is not None
    )
    vt_rate = (tariff.price_vt + dist) if can_cost else 0.0
    nt_rate = (tariff.price_nt + dist) if can_cost else 0.0

    for day in days:
        date = str(day.get("date"))
        try:
            y, m, d = (int(x) for x in date.split("-"))
        except ValueError:
            continue
        for hour, tot, hvt, hnt in hourly_energy(day, tariff):
            # fold=0 = first (CEST) instance of an ambiguous wall-clock hour on the
            # autumn DST switch; the rare doubled 02:xx hour then collapses into one
            # bucket (daily/monthly totals stay correct, only that hour's granularity).
            start = datetime(y, m, d, hour, tzinfo=tzinfo, fold=0)
            cons.append((start, tot))
            vt.append((start, hvt))
            nt.append((start, hnt))
            if can_cost:
                cost.append((start, hvt * vt_rate + hnt * nt_rate))

    cur = tariff.currency
    series = [
        (f"{DOMAIN}:grid_consumption", "SSE Grid consumption", "kWh", "energy", cons),
        (f"{DOMAIN}:grid_consumption_vt", "SSE Grid consumption VT", "kWh", "energy", vt),
        (f"{DOMAIN}:grid_consumption_nt", "SSE Grid consumption NT", "kWh", "energy", nt),
    ]
    if can_cost:
        series.append((f"{DOMAIN}:grid_cost", "SSE Grid cost", cur, None, cost))
    return series


def _to_ts(value) -> float:
    return value.timestamp() if hasattr(value, "timestamp") else float(value)


async def async_import_statistics(
    hass: HomeAssistant, tariff: TariffConfig, days: list[dict], tz: str
) -> int:
    imported = 0
    for stat_id, name, unit, unit_class, points in _series(days, tariff, tz):
        if not points:
            continue
        points.sort(key=lambda p: p[0])

        last = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, stat_id, True, {"sum"}
        )
        last_sum = 0.0
        last_start: float | None = None
        if last and last.get(stat_id):
            row = last[stat_id][0]
            last_sum = float(row.get("sum") or 0.0)
            if row.get("start") is not None:
                last_start = _to_ts(row["start"])

        running = last_sum
        new_stats: list[StatisticData] = []
        for dt, value in points:
            if last_start is not None and dt.timestamp() <= last_start:
                continue
            running += value
            new_stats.append(StatisticData(start=dt, state=round(running, 3), sum=round(running, 3)))
        if not new_stats:
            continue

        metadata: StatisticMetaData = {
            "has_mean": False,
            "has_sum": True,
            "name": name,
            "source": DOMAIN,
            "statistic_id": stat_id,
            "unit_of_measurement": unit,
        }
        if _NEW_META:
            metadata["mean_type"] = _MEAN_NONE  # type: ignore[typeddict-unknown-key]
            metadata["unit_class"] = unit_class  # type: ignore[typeddict-unknown-key]

        async_add_external_statistics(hass, metadata, new_stats)
        imported += len(new_stats)
        _LOGGER.debug("imported %d points into %s (sum=%.3f)", len(new_stats), stat_id, running)
    return imported
