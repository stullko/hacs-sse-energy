"""Sensors: live estimate, official yearly totals/costs, prices, finances, diagnostics.

(Energy-dashboard hourly history is provided separately via imported statistics.)
Entity names come from translations (translation_key); see strings.json / translations.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL
from .coordinator import SseEnergyCoordinator


@dataclass(frozen=True, kw_only=True)
class SseSensorEntityDescription(SensorEntityDescription):
    value_fn: Callable[[dict], object]
    unit_kind: str | None = None  # kwh | money | money_per_kwh | timestamp | ampere | text
    reset_key: str | None = None  # state key holding the period start (-> last_reset)


def _v(key: str) -> Callable[[dict], object]:
    return lambda data: data.get(key)


def _yesno(key: str) -> Callable[[dict], object]:
    def fn(data):
        v = data.get(key)
        if v is None:
            return None
        return "áno" if v else "nie"
    return fn


# Entity names are resolved from translations via translation_key == key; do not set
# `name=` here (it would override the translated name). Keep keys in sync with the
# "entity.sensor.*" blocks in strings.json / translations/*.json.
SENSORS: tuple[SseSensorEntityDescription, ...] = (
    # --- live current-month estimate (15-min data + contract prices) ---
    SseSensorEntityDescription(key="month_consumption", unit_kind="kwh", reset_key="month_start",
                               suggested_display_precision=2, value_fn=_v("month_consumption")),
    SseSensorEntityDescription(key="month_vt", unit_kind="kwh", reset_key="month_start", icon="mdi:weather-sunny",
                               suggested_display_precision=2, value_fn=_v("month_vt")),
    SseSensorEntityDescription(key="month_nt", unit_kind="kwh", reset_key="month_start", icon="mdi:weather-night",
                               suggested_display_precision=2, value_fn=_v("month_nt")),
    SseSensorEntityDescription(key="month_cost", unit_kind="money", icon="mdi:cash",
                               suggested_display_precision=2, value_fn=_v("month_cost")),
    SseSensorEntityDescription(key="yesterday_consumption", unit_kind="kwh", reset_key="yesterday_start",
                               suggested_display_precision=2, value_fn=_v("yesterday_consumption")),
    SseSensorEntityDescription(key="yesterday_vt", unit_kind="kwh", reset_key="yesterday_start", icon="mdi:weather-sunny",
                               suggested_display_precision=2, value_fn=_v("yesterday_vt")),
    SseSensorEntityDescription(key="yesterday_nt", unit_kind="kwh", reset_key="yesterday_start", icon="mdi:weather-night",
                               suggested_display_precision=2, value_fn=_v("yesterday_nt")),
    # --- official latest completed billing year (authoritative, from SSE) ---
    SseSensorEntityDescription(key="year_consumption", unit_kind="kwh", reset_key="year_start",
                               suggested_display_precision=2, value_fn=_v("year_consumption")),
    SseSensorEntityDescription(key="year_vt", unit_kind="kwh", reset_key="year_start", icon="mdi:weather-sunny",
                               suggested_display_precision=2, value_fn=_v("year_vt")),
    SseSensorEntityDescription(key="year_nt", unit_kind="kwh", reset_key="year_start", icon="mdi:weather-night",
                               suggested_display_precision=2, value_fn=_v("year_nt")),
    SseSensorEntityDescription(key="year_energy_cost", unit_kind="money",
                               icon="mdi:cash", suggested_display_precision=2, value_fn=_v("year_energy_cost")),
    SseSensorEntityDescription(key="year_distribution_cost", unit_kind="money",
                               icon="mdi:transmission-tower", suggested_display_precision=2, value_fn=_v("year_distribution_cost")),
    SseSensorEntityDescription(key="year_total_cost", unit_kind="money",
                               icon="mdi:cash-multiple", suggested_display_precision=2, value_fn=_v("year_total_cost")),
    SseSensorEntityDescription(key="year_period", unit_kind="text",
                               icon="mdi:calendar-range", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("year_period")),
    # --- ongoing-year estimate: paid advances vs running cost ---
    SseSensorEntityDescription(key="year_estimate_status", unit_kind="text",
                               icon="mdi:scale-balance", value_fn=_v("year_estimate_status")),
    SseSensorEntityDescription(key="year_estimate_net", unit_kind="money",
                               icon="mdi:scale-balance", suggested_display_precision=2, value_fn=_v("year_estimate_net")),
    SseSensorEntityDescription(key="year_to_date_consumption", unit_kind="kwh", reset_key="ytd_start",
                               suggested_display_precision=2, value_fn=_v("year_to_date_consumption")),
    SseSensorEntityDescription(key="year_to_date_cost", unit_kind="money",
                               icon="mdi:cash", suggested_display_precision=2, value_fn=_v("year_to_date_cost")),
    SseSensorEntityDescription(key="year_to_date_paid", unit_kind="money",
                               icon="mdi:cash-check", suggested_display_precision=2, value_fn=_v("year_to_date_paid")),
    SseSensorEntityDescription(key="current_billing_period", unit_kind="text",
                               icon="mdi:calendar-range", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("current_billing_period")),
    # --- prices / contract (from API) ---
    SseSensorEntityDescription(key="price_vt", unit_kind="money_per_kwh", icon="mdi:cash",
                               suggested_display_precision=4, value_fn=_v("price_vt")),
    SseSensorEntityDescription(key="price_nt", unit_kind="money_per_kwh", icon="mdi:cash",
                               suggested_display_precision=4, value_fn=_v("price_nt")),
    SseSensorEntityDescription(key="distribution_rate_calibrated",
                               unit_kind="money_per_kwh", icon="mdi:transmission-tower",
                               entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=5,
                               value_fn=_v("distribution_rate_calibrated")),
    SseSensorEntityDescription(key="product", unit_kind="text", icon="mdi:tag",
                               value_fn=_v("product")),
    SseSensorEntityDescription(key="distribution_tariff", unit_kind="text",
                               icon="mdi:transmission-tower", value_fn=_v("distribution_tariff")),
    SseSensorEntityDescription(key="main_breaker", unit_kind="ampere", icon="mdi:fuse",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("main_breaker")),
    SseSensorEntityDescription(key="phases", unit_kind="text", icon="mdi:sine-wave",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("phases")),
    SseSensorEntityDescription(key="distribution_company", unit_kind="text",
                               icon="mdi:office-building", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("distribution_company")),
    SseSensorEntityDescription(key="meter_serial", unit_kind="text", icon="mdi:counter",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("meter_serial")),
    SseSensorEntityDescription(key="meter_type", unit_kind="text", icon="mdi:counter",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("meter_type")),
    SseSensorEntityDescription(key="contract_number", unit_kind="text", icon="mdi:file-document",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("contract_number")),
    SseSensorEntityDescription(key="invoice_cycle", unit_kind="text", icon="mdi:calendar-sync",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("invoice_cycle")),
    # --- finances ---
    SseSensorEntityDescription(key="balance_outstanding", unit_kind="money", icon="mdi:cash-clock",
                               suggested_display_precision=2, value_fn=_v("balance_outstanding")),
    SseSensorEntityDescription(key="balance_excess", unit_kind="money", icon="mdi:cash-refund",
                               suggested_display_precision=2, value_fn=_v("balance_excess")),
    SseSensorEntityDescription(key="balance_status", unit_kind="text", icon="mdi:scale-balance",
                               value_fn=_v("balance_status")),
    SseSensorEntityDescription(key="balance_net", unit_kind="money", icon="mdi:scale-balance",
                               suggested_display_precision=2, value_fn=_v("balance_net")),
    SseSensorEntityDescription(key="last_settlement_amount", unit_kind="money",
                               icon="mdi:file-document-check", suggested_display_precision=2, value_fn=_v("last_settlement_amount")),
    SseSensorEntityDescription(key="last_settlement_period", unit_kind="text",
                               icon="mdi:calendar-range", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_settlement_period")),
    SseSensorEntityDescription(key="last_settlement_due", unit_kind="text",
                               icon="mdi:calendar-alert", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_settlement_due")),
    SseSensorEntityDescription(key="last_settlement_status", unit_kind="text",
                               icon="mdi:scale-balance", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_settlement_status")),
    SseSensorEntityDescription(key="last_payment_amount", unit_kind="money", icon="mdi:cash-check",
                               suggested_display_precision=2, value_fn=_v("last_payment_amount")),
    SseSensorEntityDescription(key="last_payment_date", unit_kind="text",
                               icon="mdi:calendar-check", entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_payment_date")),
    SseSensorEntityDescription(key="payments_paid_sum", unit_kind="money", icon="mdi:cash-multiple",
                               entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=2, value_fn=_v("payments_paid_sum")),
    SseSensorEntityDescription(key="advance_amount", unit_kind="money", icon="mdi:cash-sync",
                               suggested_display_precision=2, value_fn=_v("advance_amount")),
    SseSensorEntityDescription(key="advance_frequency", unit_kind="text", icon="mdi:calendar-sync",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("advance_frequency")),
    # --- diagnostics ---
    SseSensorEntityDescription(key="token_expires_at", unit_kind="timestamp", icon="mdi:key-clock",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("token_expires_at")),
    SseSensorEntityDescription(key="last_update", unit_kind="timestamp", icon="mdi:update",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_update")),
    SseSensorEntityDescription(key="last_data_date", unit_kind="text", icon="mdi:calendar-check",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_data_date")),
    SseSensorEntityDescription(key="last_fetch_status", unit_kind="text", icon="mdi:check-network",
                               entity_category=EntityCategory.DIAGNOSTIC, value_fn=_v("last_fetch_status")),
    SseSensorEntityDescription(key="portal_outage_active", unit_kind="text", icon="mdi:alert",
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
        self._attr_translation_key = description.key
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
            self._attr_state_class = SensorStateClass.TOTAL
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

    @property
    def last_reset(self):
        # period totals (month / yesterday / billing year) restart each period; without
        # last_reset the recorder would turn every drop into a bogus long-term sum
        key = self.entity_description.reset_key
        return (self.coordinator.data or {}).get(key) if key else None
