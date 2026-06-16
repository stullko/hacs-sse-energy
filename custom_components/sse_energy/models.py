"""Plain data structures (pure Python, no Home Assistant imports)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Totals:
    """Period totals (kWh + EUR) for one consumption period.

    kWh fields are always present; EUR fields are None when the API hasn't provided
    the price / distribution rate (cost is then reported as unavailable)."""

    nt_kwh: float = 0.0
    nt_eur: float | None = 0.0
    vt_kwh: float = 0.0
    vt_eur: float | None = 0.0
    total_kwh: float = 0.0
    total_eur: float | None = 0.0
    distribucia_eur: float | None = 0.0
    paid_eur: float = 0.0
    balance_eur: float | None = 0.0


@dataclass
class DayTotals:
    date: str
    vt: float
    nt: float
    total: float


@dataclass
class MonthSummary:
    ym: str
    vt_kwh: float
    nt_kwh: float
    total_kwh: float
    vt_eur: float | None
    nt_eur: float | None
    distribution_eur: float | None
    cost_eur: float | None


@dataclass
class PeriodResult:
    period_from: str
    period_to: str
    totals: Totals
    year_months: dict[str, dict[str, float]] = field(default_factory=dict)
    daily_data: list[DayTotals] = field(default_factory=list)
