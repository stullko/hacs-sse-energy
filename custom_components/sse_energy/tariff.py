"""Tariff & cost math - a faithful port of the PHP `SSE` class numeric logic.

Pure Python (no Home Assistant imports); shared, unit-tested logic.

Fidelity notes (verified against functions.php / settings.php):
  * profile-measurement returns 15-min readings; `value` is average power (kW).
    Energy = raw sum / 4, applied at aggregation points only (roundkwh).
  * distribucia() converts kWh -> MWh (/1000); *_pevna is a flat EUR/month charge.
    Tariff constants are already VAT-inclusive (settings.php * $dph).
  * PHP round() is half-away-from-zero -> replicated via Decimal(ROUND_HALF_UP)
    using the shortest float repr (PHP runs serialize_precision=-1).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .models import DayTotals, MonthSummary, PeriodResult, Totals


def php_round(value: float, ndigits: int = 2) -> float:
    """Replicate PHP's round($value, 2) (half away from zero)."""
    q = Decimal(1).scaleb(-ndigits)
    return float(Decimal(repr(float(value))).quantize(q, rounding=ROUND_HALF_UP))


def roundkwh(power_sum: float) -> float:
    """Port of SSE::roundkwh(): round(power_sum / 4, 2)."""
    return php_round(power_sum / 4.0, 2)


@dataclass(frozen=True)
class TariffConfig:
    """Effective (VAT-inclusive) tariff parameters, sourced live from the API.

    `price_vt` / `price_nt` are EUR/kWh incl. VAT; `dist_rate` is the calibrated
    all-in distribution EUR/kWh (official yearly distribution fee / total kWh).
    Any of them may be None when the API hasn't supplied the data yet - cost is
    then reported as unavailable while kWh are still counted. No prices are ever
    hardcoded; the coordinator fills these in from the live contract + calibration.
    """

    price_vt: float | None = None
    price_nt: float | None = None
    dist_rate: float | None = None
    vt_hours: tuple[int, ...] = ()
    currency: str = "EUR"

    def is_vt(self, hour: int) -> bool:
        return hour in self.vt_hours


def distribucia(vt_kwh: float, nt_kwh: float, t: TariffConfig) -> float | None:
    """Distribution charge for one month from the calibrated EUR/kWh rate.

    Returns None when no calibrated rate is available (e.g. no completed billing
    year yet) - there is no hardcoded distribution formula fallback."""
    if t.dist_rate is None:
        return None
    return php_round(t.dist_rate * (vt_kwh + nt_kwh), 2)


def _hour_of(period_from: str) -> int:
    return int(period_from.split(":")[0])


def compute_period(
    consumption_obj: dict,
    payment_obj: dict | None,
    tariff: TariffConfig,
    period_from: str,
    period_to: str,
    source: str = "consumption",
) -> PeriodResult:
    """Port of SSE::processConsumptionData()."""
    paid = 0.0
    if payment_obj:
        for adv in (payment_obj.get("advancePayments") or []):
            if adv.get("status") == "PAID":
                paid += float((adv.get("totalAmount") or {}).get("value") or 0.0)

    year_months: dict[str, dict[str, float]] = {}
    vt_power_total = 0.0
    nt_power_total = 0.0
    total_power = 0.0
    daily: list[DayTotals] = []

    electricity = (consumption_obj or {}).get("electricity") or {}
    for day in (electricity.get(source) or []):
        date = day.get("date")
        ym = str(date)[:7]
        day_sum = day_vt = day_nt = 0.0
        ym_bucket = year_months.setdefault(ym, {"vt": 0.0, "nt": 0.0, "sum": 0.0})

        for slot in (day.get("data") or []):
            hour = _hour_of(slot.get("periodFrom", "0:00"))
            value = float(slot.get("value") or 0.0)
            if tariff.is_vt(hour):
                day_vt += value
                vt_power_total += value
                ym_bucket["vt"] += value
            else:
                day_nt += value
                nt_power_total += value
                ym_bucket["nt"] += value
            day_sum += value
            total_power += value
            ym_bucket["sum"] += value

        daily.append(
            DayTotals(date=date, nt=roundkwh(day_nt), vt=roundkwh(day_vt), total=roundkwh(day_sum))
        )

    vt_kwh = roundkwh(vt_power_total)
    nt_kwh = roundkwh(nt_power_total)
    total_kwh = roundkwh(total_power)

    vt_eur = php_round(vt_kwh * tariff.price_vt, 2) if tariff.price_vt is not None else None
    nt_eur = php_round(nt_kwh * tariff.price_nt, 2) if tariff.price_nt is not None else None

    distribution_total: float | None = None
    if tariff.dist_rate is not None:
        distribution_total = 0.0
        for bucket in year_months.values():
            month_nt = roundkwh(bucket["nt"])
            month_vt = roundkwh(bucket["vt"])
            distribution_total += distribucia(month_vt, month_nt, tariff)
        distribution_total = php_round(distribution_total, 2)

    if vt_eur is not None and nt_eur is not None and distribution_total is not None:
        total_eur = php_round(vt_eur + nt_eur + distribution_total, 2)
        balance = paid - total_eur  # PHP returns raw $paid - $finalTotal
    else:
        total_eur = None
        balance = None

    totals = Totals(
        nt_kwh=nt_kwh,
        nt_eur=nt_eur,
        vt_kwh=vt_kwh,
        vt_eur=vt_eur,
        total_kwh=total_kwh,
        total_eur=total_eur,
        distribucia_eur=distribution_total,
        paid_eur=paid,        # PHP returns raw $paid
        balance_eur=balance,
    )
    return PeriodResult(
        period_from=period_from,
        period_to=period_to,
        totals=totals,
        year_months=year_months,
        daily_data=daily,
    )


def month_summary(year_months: dict[str, dict[str, float]], ym: str, t: TariffConfig) -> MonthSummary:
    bucket = year_months.get(ym, {"vt": 0.0, "nt": 0.0, "sum": 0.0})
    vt_kwh = roundkwh(bucket["vt"])
    nt_kwh = roundkwh(bucket["nt"])
    total_kwh = roundkwh(bucket["sum"])
    vt_eur = php_round(vt_kwh * t.price_vt, 2) if t.price_vt is not None else None
    nt_eur = php_round(nt_kwh * t.price_nt, 2) if t.price_nt is not None else None
    dist_eur = distribucia(vt_kwh, nt_kwh, t)  # None when no calibrated rate
    if vt_eur is not None and nt_eur is not None and dist_eur is not None:
        cost_eur = php_round(vt_eur + nt_eur + dist_eur, 2)
    else:
        cost_eur = None
    return MonthSummary(
        ym=ym,
        vt_kwh=vt_kwh,
        nt_kwh=nt_kwh,
        total_kwh=total_kwh,
        vt_eur=vt_eur,
        nt_eur=nt_eur,
        distribution_eur=dist_eur,
        cost_eur=cost_eur,
    )


def hourly_energy(day: dict, tariff: TariffConfig) -> list[tuple[int, float, float, float]]:
    """Group a day's 15-min slots into hourly energy (kWh): (hour, total, vt, nt)."""
    buckets: dict[int, list[float]] = {}
    for slot in (day.get("data") or []):
        hour = _hour_of(slot.get("periodFrom", "0:00"))
        value = float(slot.get("value") or 0.0)
        buckets.setdefault(hour, [0.0, 0.0, 0.0])
        energy = value / 4.0
        buckets[hour][0] += energy
        if tariff.is_vt(hour):
            buckets[hour][1] += energy
        else:
            buckets[hour][2] += energy
    out: list[tuple[int, float, float, float]] = []
    for hour in sorted(buckets):
        tot, vt, nt = buckets[hour]
        out.append((hour, tot, vt, nt))
    return out


def parse_vt_hours(raw) -> tuple[int, ...]:
    """Normalise a VT-hours option (str '0,1,10,15' or list) to a tuple of ints."""
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        return tuple(int(p) for p in raw.replace(";", ",").split(",") if p.strip() != "")
    return tuple(int(h) for h in raw)


def build_tariff(opts: dict) -> TariffConfig:
    """Skeleton tariff from options - carries only VT-hour windows and currency.

    Energy prices and the distribution rate are NOT taken from options (no hardcoded
    prices); the coordinator fills them from the live contract + yearly calibration."""
    from .const import CONF_CURRENCY, CONF_VT_HOURS

    return TariffConfig(
        vt_hours=parse_vt_hours(opts.get(CONF_VT_HOURS)),
        currency=str(opts.get(CONF_CURRENCY, "EUR")),
    )
