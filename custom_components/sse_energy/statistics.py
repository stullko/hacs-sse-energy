"""Import accurate hourly long-term statistics into Home Assistant.

External statistics (statistic_id contains ':') carry historical timestamps, so
the Energy dashboard shows correct hourly/daily bars despite SSE's 1-2 day delay.
This is the same pattern utility integrations like `opower` use.

Revising: every run rewrites the last REVISE_DAYS days (plus any gap after the last
stored hour) from the sum stored just before that window. SSE first publishes
preliminary data and finalizes it later; a forward-only import would keep the
preliminary (or partial) values forever. Rewriting is safe - the recorder upserts
by id+start and the sum stays continuous because it is rebuilt from the base.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .series import COST_SERIES, CONSUMPTION_SERIES, HOUR, build_series, cumulate, revision_window
from .tariff import TariffConfig

_LOGGER = logging.getLogger(__name__)

REVISE_DAYS = 30  # must stay within the coordinator's recent (SHORT-mode) profile window

try:  # mean_type/unit_class are required from HA Core 2026.11 (added 2025.11)
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_NONE = StatisticMeanType.NONE
    _NEW_META = True
except ImportError:  # pragma: no cover - older HA
    _MEAN_NONE = None
    _NEW_META = False


def _to_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromtimestamp(float(value), timezone.utc)


def _sum_before(hass: HomeAssistant, stat_id: str, start: datetime) -> float | None:
    """Stored `sum` of the last hour before `start` (None if there is none within a week)."""
    rows = statistics_during_period(
        hass, start - timedelta(days=7), start, {stat_id}, "hour", None, {"sum"}
    ).get(stat_id)
    if not rows:
        return None
    return float(rows[-1].get("sum") or 0.0)


async def async_import_statistics(
    hass: HomeAssistant, tariff: TariffConfig, days: list[dict], tz: str
) -> int:
    series = build_series(days, tariff, tz)
    today = datetime.now(ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    revise_from = (today - timedelta(days=REVISE_DAYS)).astimezone(timezone.utc)
    recorder = get_instance(hass)
    imported = 0

    for suffix, points in series.items():
        stat_id = f"{DOMAIN}:{suffix}"
        last = await recorder.async_add_executor_job(
            get_last_statistics, hass, 1, stat_id, True, {"sum"}
        )
        last_sum = 0.0
        last_start: datetime | None = None
        if last and last.get(stat_id):
            row = last[stat_id][0]
            last_sum = float(row.get("sum") or 0.0)
            if row.get("start") is not None:
                last_start = _to_dt(row["start"])

        window = revision_window(points, last_start, revise_from)
        if window is None:
            continue
        start, end = window
        if last_start is None or last_start < start:
            base = last_sum
        else:
            base = await recorder.async_add_executor_job(_sum_before, hass, stat_id, start)
            if base is None:  # hole before the window: fall back to forward-only
                start, base = last_start + HOUR, last_sum
                if start > end:
                    continue

        rows = cumulate(points, start, end, base)
        new_stats = [StatisticData(start=dt, state=s, sum=s) for dt, s in rows]
        is_cost = suffix in COST_SERIES
        metadata: StatisticMetaData = {
            "has_mean": False,
            "has_sum": True,
            "name": COST_SERIES[suffix] if is_cost else CONSUMPTION_SERIES[suffix],
            "source": DOMAIN,
            "statistic_id": stat_id,
            "unit_of_measurement": tariff.currency if is_cost else "kWh",
        }
        if _NEW_META:
            metadata["mean_type"] = _MEAN_NONE  # type: ignore[typeddict-unknown-key]
            metadata["unit_class"] = None if is_cost else "energy"  # type: ignore[typeddict-unknown-key]

        async_add_external_statistics(hass, metadata, new_stats)
        imported += len(new_stats)
        _LOGGER.debug(
            "wrote %d hours into %s (%s .. %s, sum=%.3f)", len(new_stats), stat_id, start, end, rows[-1][1]
        )
    return imported
