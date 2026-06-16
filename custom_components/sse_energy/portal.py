"""Pure parsers for the SSE eZona portal JSON payloads (no Home Assistant imports).

Shapes verified against the live portal (2026-06):
  * delivery-point/v1/{point}                -> contract: prices, product, distribution
  * delivery-point/v1/{point}/consumption    -> official per-billing-year totals + fees
  * invoice/v1/balance                       -> outstanding / excess
  * invoice/v1?...                           -> invoices[] (settlement / advances)
  * payment/v1?...                           -> paidSum + payments[]
  * selfreport/v1/.../additional-info        -> meter type + serial
  * anonymous/system-info/v1/outage          -> portal outage banner
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


def _g(d, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class ContractInfo:
    price_vt_mwh: float | None = None   # EUR/MWh as shown on the portal
    price_nt_mwh: float | None = None
    product: str | None = None          # e.g. "DD5"
    distr_tariff: str | None = None     # e.g. "D5"
    distr_company: str | None = None
    breaker_a: float | None = None
    phases: int | None = None
    voltage: str | None = None
    meter_serial: str | None = None
    contract_number: str | None = None
    invoice_cycle: str | None = None
    pod_number: int | None = None


def parse_delivery_point(dp: dict) -> ContractInfo:
    merchant = _g(dp, "merchant", default={}) or {}
    pv = pn = None
    for p in (merchant.get("price") or []):
        remark = (p.get("remark1") or "").upper()
        if "VT" in remark:
            pv = _f(p.get("value"))
        elif "NT" in remark:
            pn = _f(p.get("value"))
    dist = _g(dp, "distribution", default={}) or {}
    meters = dist.get("meteringDevice") or []
    serial = _g(meters[0], "serialNumber", "value") if meters else None
    contract = _g(merchant, "contractNumber", "value")
    return ContractInfo(
        price_vt_mwh=pv,
        price_nt_mwh=pn,
        product=_g(merchant, "productName", "value"),
        distr_tariff=_g(dist, "distrTariff", "description") or _g(dist, "distrTariff", "value"),
        distr_company=_g(dist, "distrCompany", "value"),
        breaker_a=_f(_g(dist, "mainCircuitBreakerValue", "value")),
        phases=_g(dist, "phaseNumber", "value"),
        voltage=_g(dist, "voltageLevel", "value"),
        meter_serial=serial,
        contract_number=str(contract) if contract is not None else None,
        invoice_cycle=_g(merchant, "invoiceCycle", "value"),
        pod_number=_g(dp, "general", "podNumber", "value"),
    )


@dataclass
class DeliveryPointRef:
    """One delivery point as listed by /user/v1/info (for the config-flow picker)."""

    id: str
    eic: str | None = None
    name: str | None = None
    city: str | None = None
    commodity: str | None = None
    state: str | None = None

    @property
    def label(self) -> str:
        """Human-friendly label for the selection dropdown."""
        base = self.name or self.city or self.id
        return f"{base} ({self.eic})" if self.eic else str(base)


@dataclass
class UserInfo:
    delivery_points: list[DeliveryPointRef]
    commodity_types: list[str]
    min_date_from: str | None = None

    @property
    def electricity_points(self) -> list[DeliveryPointRef]:
        return [p for p in self.delivery_points if (p.commodity or "").upper() == "ELECTRICITY"]


def parse_user_info(d: dict) -> UserInfo:
    """Parse /user/v1/info: the customer's delivery points + data-history bounds."""
    points = [
        DeliveryPointRef(
            id=p.get("id"),
            eic=p.get("eic"),
            name=p.get("name"),
            city=p.get("city"),
            commodity=p.get("commodityType"),
            state=p.get("state"),
        )
        for p in (d.get("deliveryPoints") or [])
        if p.get("id")
    ]
    return UserInfo(
        delivery_points=points,
        commodity_types=list(d.get("commodityTypes") or []),
        min_date_from=_g(d, "parameters", "consumption", "filterMinimalDateFrom"),
    )


@dataclass
class DeliveryPointInfo:
    """Parsed /delivery-point/v1/{id}/delivery-point-info (tariff + advance payment)."""

    tariff: str | None = None
    advance_amount: float | None = None
    advance_frequency: str | None = None
    owed_amount: float | None = None


def parse_delivery_point_info(d: dict) -> DeliveryPointInfo:
    return DeliveryPointInfo(
        tariff=d.get("tariff"),
        advance_amount=_f(_g(d, "advancedPayment", "amount", "value")),
        advance_frequency=_g(d, "advancedPayment", "frequency"),
        owed_amount=_f(_g(d, "owedAmount", "value")),
    )


@dataclass
class YearSummary:
    period_from: str | None
    period_to: str | None
    nt_kwh: float
    vt_kwh: float
    total_kwh: float
    energy_eur: float       # silová (powerEnergy)
    distribution_eur: float
    total_eur: float        # totalSum


def parse_consumption_summary(cs: dict) -> list[YearSummary]:
    out: list[YearSummary] = []
    for y in (cs.get("consumption") or []):
        el = y.get("electricity") or {}
        fees = y.get("fees") or {}

        def mwh_to_kwh(key):
            v = _f(_g(el, key, "value"))
            return round(v * 1000.0, 3) if v is not None else 0.0

        out.append(YearSummary(
            period_from=y.get("periodFrom"),
            period_to=y.get("periodTo"),
            nt_kwh=mwh_to_kwh("lowTariff"),
            vt_kwh=mwh_to_kwh("highTariff"),
            total_kwh=mwh_to_kwh("total"),
            energy_eur=_f(_g(fees, "powerEnergy", "value"), 0.0),
            distribution_eur=_f(_g(fees, "distribution", "value"), 0.0),
            total_eur=_f(_g(fees, "totalSum", "value"), 0.0),
        ))
    out.sort(key=lambda s: s.period_from or "")
    return out


def latest_year(summaries: list[YearSummary]) -> YearSummary | None:
    return summaries[-1] if summaries else None


def parse_balance(b: dict) -> dict:
    return {
        "outstanding": _f(_g(b, "outstanding", "value"), 0.0),
        "excess": _f(_g(b, "excess", "value"), 0.0),
    }


def classify_balance(outstanding, excess) -> tuple[str, float]:
    """Return (status, net) where net>0 = Preplatok, net<0 = Nedoplatok.

    net = excess - outstanding  (positive = credit/overpayment in your favour).
    """
    net = round((excess or 0.0) - (outstanding or 0.0), 2)
    if net > 0:
        status = "Preplatok"
    elif net < 0:
        status = "Nedoplatok"
    else:
        status = "Vyrovnané"
    return status, net


def current_billing_period(latest_period_to, today) -> tuple[str, str]:
    """(start, end) ISO of the ongoing billing year = day after the last completed year."""
    if latest_period_to:
        try:
            y, m, d = (int(x) for x in latest_period_to.split("-"))
            start = date(y, m, d) + timedelta(days=1)
            end = date(start.year + 1, start.month, start.day) - timedelta(days=1)
            return start.isoformat(), end.isoformat()
        except (ValueError, AttributeError):
            pass
    yr = today.year if today.month >= 7 else today.year - 1
    return f"{yr}-07-01", f"{yr + 1}-06-30"


def sum_advances_since(payments_json: dict, since_iso: str) -> float:
    """Sum advance payments (excluding settlement-invoice payments) on/after since_iso."""
    total = 0.0
    for p in (payments_json.get("payments") or []):
        d = p.get("date")
        if not d or d < since_iso:
            continue
        types = " ".join(str(ri.get("type", "")) for ri in (p.get("relatedInvoice") or []))
        if "SETTLEMENT" in types.upper():
            continue
        total += _f(_g(p, "amount", "value"), 0.0) or 0.0
    return round(total, 2)


def status_from_net(net: float) -> str:
    return "Preplatok" if net > 0 else ("Nedoplatok" if net < 0 else "Vyrovnané")


def parse_invoices(inv: dict) -> dict:
    invoices = inv.get("invoices") or []
    settlements = [i for i in invoices if i.get("type") == "ELECTRICITY_SETTLEMENT_INVOICE"]
    settlements.sort(key=lambda i: i.get("dateTo") or "", reverse=True)
    s = settlements[0] if settlements else None
    if not s:
        return {"settlement": None}
    bt = s.get("balanceType") or _g(s, "totalAmount", "balanceType")
    status = {"EXCESS_PAYMENTS": "Preplatok", "OUTSTANDING_PAYMENTS": "Nedoplatok"}.get(bt)
    return {"settlement": {
        "period_from": s.get("dateFrom"),
        "period_to": s.get("dateTo"),
        "amount": _f(_g(s, "totalAmount", "value"), 0.0),
        "due_date": s.get("dueDate"),
        "paid": bool(s.get("paid")),
        "variable_symbol": s.get("variableSymbol"),
        "status": status,
    }}


def parse_payments(p: dict) -> dict:
    payments = p.get("payments") or []
    last = payments[0] if payments else None  # API returns newest first
    return {
        "paid_sum": _f(_g(p, "paidSum", "value"), 0.0),
        "overpayment_sum": _f(_g(p, "overPaymentSum", "value"), 0.0),
        "last": ({
            "date": last.get("date"),
            "amount": _f(_g(last, "amount", "value"), 0.0),
            "method": last.get("method"),
        } if last else None),
    }


def parse_meter_info(m: dict) -> dict:
    meters = m.get("meters") or []
    return {
        "meter_type": m.get("type"),
        "serial": (meters[0].get("serialNumber") if meters else None),
    }


def parse_outage(o: dict) -> dict:
    return {
        "enabled": bool(o.get("enabled")),
        "active": bool(o.get("active")),
        "title": o.get("title"),
        "message": o.get("message"),
        "start": o.get("start"),
        "end": o.get("end"),
    }
