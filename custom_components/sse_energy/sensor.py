"""Sensors: live estimate, official yearly totals/costs, prices, finances, diagnostics.

(Energy-dashboard hourly history is provided separately via imported statistics.)
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_info import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL
from .coordinator import SseEnergyCoordinator


@dataclass(frozen=True, kw_only=True)
class SseSensorEntityDescription(SensorEntityDescription):
    value_fn: Callable[[dict], object]
    unit_kind: str | None = None  # kwh | money | money_per_kwh | timestamp | ampere | text


def _v(key: str) -> Callable[[dict], object]:
    return lambda data: data.get(key)


def _yesno(key: str) -> Callable[[dict], object]:
    def fn(data):
        v = data.get(key)
        if v is None:
            return None
        return "áno" if v else "nie"
    return fn


SENSORS: tuple[SseSensorEntityDescription, ...] = (
    # --- live current-month estimate (15-min data + contract prices) ---
    SseSensorEntityDescription(key="month_consumption", name="This month consumption", unit_kind="kwh",
                               suggested_display_precision=2, value_fn=_v("month_consumption")),
    SseSensorEntityDescription(key="month_vt", name="This month VT", unit_kind="kwh", icon="mdi:weather-sunny",
                               suggested_display_precision=2, value_fn=_v("month_vt")),
    SseSensorEntityDescription(key="month_nt", name="This month NT", unit_kind="kwh", icon="mdi:weather-night",
                               suggested_display_precision=2, value_fn=_v("month_nt")),
    SseSensorEntityDescription(key="month_cost", name="This month cost (est.)", unit_kind="money", icon="mdi:cash",
                               suggested_display_precision=2, value_fn=_v("month_cost")),
    SseSensorEntityDescription(key="yesterday_consumption", name="Yesterday consumption", unit_kind="kwh",
                               suggested_display_precision=2, value_fn=_v("yesterday_consumption")),
    SseSensorEntityDescription(key="yesterday_vt", name="Yesterday VT", unit_kind="kwh", icon="mdi:weather-sunny",
                               suggested_display_precision=2, value_fn=_v("yesterday_vt")),
    SseSensorEntityDescription(key="yesterday_nt", name="Yesterday NT", unit_kind="kwh", icon="mdi:weather-night",
                               suggested_display_precision=2, value_fn=_v("yesterday_nt")),
    # --- official latest completed billing year (authoritative, from SSE) ---
    SseSensorEntityDescription(key="year_consumption", name="Billing year consumption", unit_kind="kwh",
                               suggested_display_precision=2, value_fn=_v("year_consumption")),
    SseSensorEntityDescription(key="year_vt", name="Billing year VT", unit_kind="kwh", icon="mdi:weather-sunny",
                               suggested_display_precision=2, value_fn=_v("year_vt")),
    SseSensorEntityDescription(key="year_nt", name="Billing year NT", unit_kind="kwh", icon="mdi:weather-night",
                               suggested_display_precision=2, value_fn=_v("year_nt")),
    SseSensorEntityDescription(key="year_energy_cost", name="Billing year energy cost", unit_kind="money",
                               icon="mdi:cash", suggested_display_precision=2, value_fn=_v("year_energy_cost")),
    SseSensorEntityDescription(key="year_distribution_cost", name="Billing year distribution cost", unit_kind="money",
                               icon="mdi:transmission-tower", suggested_display_precision=2, value_fn=_v("year_distribution_cost")),
    SseSensorEntityDescription(key="year_total_cost", name="Billing year total cost", unit_kind="money",
                               icon="mdi:cash-multiple", suggested_display_precision=2, value_fn=_v("year_total_cost")),
    SseSensorEntityDescription(key="year_period", name="Billing year period", unit_kind="text",
                               icon="mdi:calendar-range", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("year_period")),
    # --- ongoing-year estimate: paid advances vs running cost ---
    SseSensorEntityDescription(key="year_estimate_status", name="This year estimate", unit_kind="text",
                               icon="mdi:scale-balance", value_fn=_v("year_estimate_status")),
    SseSensorEntityDescription(key="year_estimate_net", name="This year estimate (net)", unit_kind="money",
                               icon="mdi:scale-balance", suggested_display_precision=2, value_fn=_v("year_estimate_net")),
    SseSensorEntityDescription(key="year_to_date_consumption", name="This year consumption", unit_kind="kwh",
                               suggested_display_precision=2, value_fn=_v("year_to_date_consumption")),
    SseSensorEntityDescription(key="year_to_date_cost", name="This year cost (est.)", unit_kind="money",
                               icon="mdi:cash", suggested_display_precision=2, value_fn=_v("year_to_date_cost")),
    SseSensorEntityDescription(key="year_to_date_paid", name="This year advances paid", unit_kind="money",
                               icon="mdi:cash-check", suggested_display_precision=2, value_fn=_v("year_to_date_paid")),
    SseSensorEntityDescription(key="current_billing_period", name="Current billing period", unit_kind="text",
                               icon="mdi:calendar-range", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("current_billing_period")),
    # --- prices / contract (from API) ---
    SseSensorEntityDescription(key="price_vt", name="VT price", unit_kind="money_per_kwh", icon="mdi:cash",
                               suggested_display_precision=4, value_fn=_v("price_vt")),
    SseSensorEntityDescription(key="price_nt", name="NT price", unit_kind="money_per_kwh", icon="mdi:cash",
                               suggested_display_precision=4, value_fn=_v("price_nt")),
    SseSensorEntityDescription(key="distribution_rate_calibrated", name="Distribution rate (calibrated)",
                               unit_kind="money_per_kwh", icon="mdi:transmission-tower",
                               entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=5,
                               value_fn=_v("distribution_rate_calibrated")),
    SseSensorEntityDescription(key="product", name="Product", unit_kind="text", icon="mdi:tag",
                               value_fn=_v("product")),
    SseSensorEntityDescription(key="distribution_tariff", name="Distribution tariff", unit_kind="text",
                               icon="mdi:transmission-tower", value_fn=_v("distribution_tariff")),
    SseSensorEntityDescription(key="main_breaker", name="Main breaker", unit_kind="ampere", icon="mdi:fuse",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("main_breaker")),
    SseSensorEntityDescription(key="phases", name="Phases", unit_kind="text", icon="mdi:sine-wave",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("phases")),
    SseSensorEntityDescription(key="distribution_company", name="Distribution company", unit_kind="text",
                               icon="mdi:office-building", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("distribution_company")),
    SseSensorEntityDescription(key="meter_serial", name="Meter serial", unit_kind="text", icon="mdi:counter",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("meter_serial")),
    SseSensorEntityDescription(key="meter_type", name="Meter type", unit_kind="text", icon="mdi:counter",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("meter_type")),
    SseSensorEntityDescription(key="contract_number", name="Contract number", unit_kind="text", icon="mdi:file-document",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("contract_number")),
    SseSensorEntityDescription(key="invoice_cycle", name="Invoice cycle", unit_kind="text", icon="mdi:calendar-sync",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("invoice_cycle")),
    # --- finances ---
    SseSensorEntityDescription(key="balance_outstanding", name="Amount due", unit_kind="money", icon="mdi:cash-clock",
                               suggested_display_precision=2, value_fn=_v("balance_outstanding")),
    SseSensorEntityDescription(key="balance_excess", name="Overpayment", unit_kind="money", icon="mdi:cash-refund",
                               suggested_display_precision=2, value_fn=_v("balance_excess")),
    SseSensorEntityDescription(key="balance_status", name="Balance status", unit_kind="text", icon="mdi:scale-balance",
                               value_fn=_v("balance_status")),
    SseSensorEntityDescription(key="balance_net", name="Balance (net)", unit_kind="money", icon="mdi:scale-balance",
                               suggested_display_precision=2, value_fn=_v("balance_net")),
    SseSensorEntityDescription(key="last_settlement_amount", name="Last settlement amount", unit_kind="money",
                               icon="mdi:file-document-check", suggested_display_precision=2, value_fn=_v("last_settlement_amount")),
    SseSensorEntityDescription(key="last_settlement_period", name="Last settlement period", unit_kind="text",
                               icon="mdi:calendar-range", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_settlement_period")),
    SseSensorEntityDescription(key="last_settlement_due", name="Last settlement due", unit_kind="text",
                               icon="mdi:calendar-alert", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_settlement_due")),
    SseSensorEntityDescription(key="last_settlement_status", name="Last settlement status", unit_kind="text",
                               icon="mdi:scale-balance", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_settlement_status")),
    SseSensorEntityDescription(key="last_payment_amount", name="Last payment", unit_kind="money", icon="mdi:cash-check",
                               suggested_display_precision=2, value_fn=_v("last_payment_amount")),
    SseSensorEntityDescription(key="last_payment_date", name="Last payment date", unit_kind="text",
                               icon="mdi:calendar-check", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_payment_date")),
    SseSensorEntityDescription(key="payments_paid_sum", name="Paid (3y)", unit_kind="money", icon="mdi:cash-multiple",
                               entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=2, value_fn=_v("payments_paid_sum")),
    SseSensorEntityDescription(key="advance_amount", name="Advance payment", unit_kind="money", icon="mdi:cash-sync",
                               suggested_display_precision=2, value_fn=_v("advance_amount")),
    SseSensorEntityDescription(key="advance_frequency", name="Advance frequency", unit_kind="text", icon="mdi:calendar-sync",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("advance_frequency")),
    # --- diagnostics ---
    SseSensorEntityDescription(key="token_expires_at", name="Token expiry", unit_kind="timestamp", icon="mdi:key-clock",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("token_expires_at")),
    SseSensorEntityDescription(key="last_update", name="Last update", unit_kind="timestamp", icon="mdi:update",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_update")),
    SseSensorEntityDescription(key="last_data_date", name="Latest data day", unit_kind="text", icon="mdi:calendar-check",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_data_date")),
    SseSensorEntityDescription(key="last_fetch_status", name="Last fetch status", unit_kind="text", icon="mdi:check-network",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_fetch_status")),
    SseSensorEntityDescription(key="portal_outage_active", name="Portal outage", unit_kind="text", icon="mdi:alert",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_yesno("portal_outage_active")),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SseEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(SseEnergySensor(coordinator, desc) for desc in SENSORS)


class SseEnergySensor(CoordinatorEntity[SseEnergyCoordinator], SensorEntity):
    entity_description: SseSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: SseEnergyCoordinator, description: SseSensorEntityDescription):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=DEFAULT_NAME,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
        cur = coordinator.currency
        kind = description.unit_kind
        if kind == "kwh":
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_device_class = SensorDeviceClass.ENERGY
        elif kind == "money":
            self._attr_native_unit_of_measurement = cur
            self._attr_device_class = SensorDeviceClass.MONETARY
        elif kind == "money_per_kwh":
            self._attr_native_unit_of_measurement = f"{cur}/kWh"
        elif kind == "ampere":
            self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        elif kind == "timestamp":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data or {})
