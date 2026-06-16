"""DataUpdateCoordinator: fetch from SSE (executor) + compute + import statistics.

Prices and tariff are auto-loaded from the portal (delivery-point detail); the
configured values are used only as a fallback. Official per-billing-year totals
and costs come straight from SSE (consumption summary) - no local tariff math.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SseAuthError, SseClient, SseError
from .const import (
    CONF_AUTH_BASE_URL,
    CONF_BASE_URL,
    CONF_ENABLE_STATISTICS,
    CONF_HTTP_TIMEOUT,
    CONF_IMPERSONATE,
    CONF_PASSWORD,
    CONF_POINT,
    CONF_SCAN_INTERVAL,
    CONF_TIMEZONE,
    CONF_TOKEN_BUFFER,
    CONF_USERNAME,
    CONF_VAT,
    DEFAULTS,
    DOMAIN,
    vt_hours_for,
)
from . import portal
from .statistics import async_import_statistics
from .tariff import build_tariff, compute_period, month_summary

_LOGGER = logging.getLogger(__name__)

HISTORY_DAYS = 60  # how far back to pull 15-min profile for month totals + stats backfill


def merged_options(entry: ConfigEntry) -> dict:
    return {**DEFAULTS, **entry.data, **entry.options}


def _collect_days(profile: dict) -> list[dict]:
    electricity = (profile or {}).get("electricity") or {}
    by_date: dict[str, dict] = {}
    for day in (electricity.get("preConsumption") or []):
        by_date[str(day.get("date"))] = day
    for day in (electricity.get("consumption") or []):
        by_date[str(day.get("date"))] = day
    return [by_date[d] for d in sorted(by_date)]


def _safe(label: str, fn, default=None):
    try:
        return fn()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("%s fetch failed: %s", label, err)
        return default


class SseEnergyCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.entry = entry
        self.opts = merged_options(entry)
        self.tz = ZoneInfo(self.opts[CONF_TIMEZONE])
        self.base_tariff = build_tariff(self.opts)   # config fallback
        self.currency = self.base_tariff.currency
        self.enable_statistics = bool(self.opts[CONF_ENABLE_STATISTICS])
        self.client = SseClient(
            username=self.opts[CONF_USERNAME],
            password=self.opts[CONF_PASSWORD],
            point=self.opts[CONF_POINT],
            base_url=self.opts[CONF_BASE_URL],
            auth_base_url=self.opts[CONF_AUTH_BASE_URL],
            timeout=int(self.opts[CONF_HTTP_TIMEOUT]),
            impersonate=self.opts[CONF_IMPERSONATE],
            token_buffer=int(self.opts[CONF_TOKEN_BUFFER]),
        )
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=int(self.opts[CONF_SCAN_INTERVAL])),
        )

    def _effective_tariff(self, contract: portal.ContractInfo, dist_rate=None, tariff_code=None):
        """Build the effective tariff entirely from the live API: energy prices from
        the contract (x configured VAT) and the calibrated distribution rate. VT/NT
        hours come from the user's option override (already in base_tariff) or, if
        unset, the preset for the contract tariff/product."""
        fields: dict = {}
        if contract and contract.price_vt_mwh and contract.price_nt_mwh:
            vat = float(self.opts[CONF_VAT])
            fields["price_vt"] = contract.price_vt_mwh / 1000.0 * vat
            fields["price_nt"] = contract.price_nt_mwh / 1000.0 * vat
        if dist_rate is not None:
            fields["dist_rate"] = dist_rate
        if not self.base_tariff.vt_hours:  # no user override -> derive from tariff preset
            product = contract.product if contract else None
            distr = contract.distr_tariff if contract else None
            preset = vt_hours_for(tariff_code) or vt_hours_for(product) or vt_hours_for(distr)
            if preset:
                fields["vt_hours"] = preset
        return replace(self.base_tariff, **fields) if fields else self.base_tariff

    def _fetch_and_compute(self) -> dict:
        """Blocking; runs in executor."""
        today = datetime.now(self.tz).date()
        ym = today.strftime("%Y-%m")
        win_from = (today - timedelta(days=3 * 365)).isoformat()
        win_to = today.isoformat()

        # --- account profile: earliest available data bounds the history we request ---
        ui = portal.parse_user_info(_safe("user-info", self.client.fetch_user_info, {}) or {})
        if ui.min_date_from and ui.min_date_from > win_from:
            win_from = ui.min_date_from

        # --- contract (prices/product/distribution) + tariff / advance payment ---
        dp = _safe("delivery-point", self.client.fetch_delivery_point, {})
        contract = portal.parse_delivery_point(dp or {})
        dpi = portal.parse_delivery_point_info(
            _safe("delivery-point-info", self.client.fetch_delivery_point_info, {}) or {}
        )

        # --- official per-year totals + costs (authoritative) ---
        cs = _safe("consumption-summary", self.client.fetch_consumption_summary, {})
        years = portal.parse_consumption_summary(cs or {})
        year = portal.latest_year(years)

        # calibrate distribution to the last official year (distribution EUR / kWh)
        calib_rate = None
        if year and year.total_kwh:
            calib_rate = year.distribution_eur / year.total_kwh
        tariff = self._effective_tariff(contract, calib_rate, dpi.tariff)

        # --- ongoing billing year (day after the last completed year) ---
        bill_from, bill_to = portal.current_billing_period(year.period_to if year else None, today)
        if ui.min_date_from and bill_from < ui.min_date_from:
            bill_from = ui.min_date_from
        # SHORT window returns current-month preConsumption; LONG window returns finalized
        # consumption up to the end of last month -> merge both for the whole year to date.
        first = today.replace(day=1)
        recent_from = (first - timedelta(days=3)).isoformat()
        profile_recent = self.client.fetch_profile(recent_from, win_to)   # core; may raise
        profile_year = _safe("year-profile", lambda: self.client.fetch_profile(bill_from, win_to), {})
        merged = list({str(d.get("date")): d
                       for d in (_collect_days(profile_year or {}) + _collect_days(profile_recent))}.values())
        ytd = compute_period({"electricity": {"consumption": merged}}, None, tariff, bill_from, win_to, source="consumption")
        month = month_summary(ytd.year_months, ym, tariff)
        yest = max(ytd.daily_data, key=lambda x: x.date) if ytd.daily_data else None
        last_data_date = yest.date if yest else None
        stats_days = merged

        # --- finances ---
        balance = portal.parse_balance(_safe("balance", self.client.fetch_balance, {}) or {})
        invoices = portal.parse_invoices(_safe("invoices", lambda: self.client.fetch_invoices(win_from, win_to), {}) or {})
        payments_raw = _safe("payments", lambda: self.client.fetch_payments(win_from, win_to), {}) or {}
        payments = portal.parse_payments(payments_raw)
        meter = portal.parse_meter_info(_safe("meter", self.client.fetch_meter_info, {}) or {})
        outage = portal.parse_outage(_safe("outage", self.client.fetch_outage, {}) or {})
        advances_ytd = portal.sum_advances_since(payments_raw, bill_from)
        if ytd.totals.total_eur is not None:
            ytd_net = round(advances_ytd - ytd.totals.total_eur, 2)
            ytd_status = portal.status_from_net(ytd_net)
        else:
            ytd_net = None
            ytd_status = None

        now = datetime.now(self.tz)
        exp = self.client.expires_at
        settlement = invoices.get("settlement") or {}
        last_pay = payments.get("last") or {}
        bal_status, bal_net = portal.classify_balance(balance.get("outstanding"), balance.get("excess"))

        state = {
            # live (current month, estimate from 15-min data + contract prices)
            "month_consumption": month.total_kwh,
            "month_vt": month.vt_kwh,
            "month_nt": month.nt_kwh,
            "month_cost": month.cost_eur,
            "yesterday_consumption": yest.total if yest else 0.0,
            "yesterday_vt": yest.vt if yest else 0.0,
            "yesterday_nt": yest.nt if yest else 0.0,
            # official latest completed billing year (from SSE)
            "year_consumption": year.total_kwh if year else None,
            "year_vt": year.vt_kwh if year else None,
            "year_nt": year.nt_kwh if year else None,
            "year_energy_cost": year.energy_eur if year else None,
            "year_distribution_cost": year.distribution_eur if year else None,
            "year_total_cost": year.total_eur if year else None,
            "year_period": (f"{year.period_from} .. {year.period_to}" if year else None),
            # ongoing-year estimate (paid advances - running cost)
            "year_to_date_consumption": ytd.totals.total_kwh,
            "year_to_date_cost": ytd.totals.total_eur,
            "year_to_date_paid": advances_ytd,
            "year_estimate_net": ytd_net,
            "year_estimate_status": ytd_status,
            "current_billing_period": f"{bill_from} .. {bill_to}",
            # prices / contract (from API)
            "price_vt": round(tariff.price_vt, 4) if tariff.price_vt is not None else None,
            "price_nt": round(tariff.price_nt, 4) if tariff.price_nt is not None else None,
            "distribution_rate_calibrated": round(calib_rate, 5) if calib_rate else None,
            "advance_amount": dpi.advance_amount,
            "advance_frequency": dpi.advance_frequency,
            "product": contract.product,
            "distribution_tariff": contract.distr_tariff,
            "distribution_company": contract.distr_company,
            "main_breaker": contract.breaker_a,
            "phases": contract.phases,
            "meter_serial": contract.meter_serial or meter.get("serial"),
            "meter_type": meter.get("meter_type"),
            "contract_number": contract.contract_number,
            "invoice_cycle": contract.invoice_cycle,
            # finances
            "balance_outstanding": balance.get("outstanding"),
            "balance_excess": balance.get("excess"),
            "balance_status": bal_status,
            "balance_net": bal_net,
            "last_settlement_amount": settlement.get("amount"),
            "last_settlement_status": settlement.get("status"),
            "last_settlement_period": (
                f"{settlement.get('period_from')} .. {settlement.get('period_to')}" if settlement else None
            ),
            "last_settlement_due": settlement.get("due_date"),
            "last_payment_amount": last_pay.get("amount"),
            "last_payment_date": last_pay.get("date"),
            "payments_paid_sum": payments.get("paid_sum"),
            # diagnostics
            "token_expires_at": datetime.fromtimestamp(exp, self.tz) if exp else None,
            "last_update": now,
            "last_data_date": last_data_date,
            "last_fetch_status": "ok",
            "portal_outage_active": outage.get("active"),
            "portal_outage_message": outage.get("title"),
        }
        return {"state": state, "days": stats_days, "tariff": tariff}

    async def _async_update_data(self) -> dict:
        try:
            result = await self.hass.async_add_executor_job(self._fetch_and_compute)
        except SseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SseError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"unexpected error: {err}") from err

        if self.enable_statistics:
            try:
                n = await async_import_statistics(
                    self.hass, result["tariff"], result["days"], self.opts[CONF_TIMEZONE]
                )
                if n:
                    _LOGGER.info("imported %d hourly statistics points", n)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("statistics import failed: %s", err)

        return result["state"]
