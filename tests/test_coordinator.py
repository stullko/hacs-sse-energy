"""HA coordinator test: full fetch chain with a mocked SseClient.

Proves the coordinator builds the tariff entirely from the live API (energy price x
VAT, calibrated distribution), resolves VT/NT hours from the tariff preset, surfaces
the advance payment, and degrades gracefully (cost None, kWh still counted) when the
API returns no prices. No network / curl_cffi / recorder is touched.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.sse_energy.const import (  # noqa: E402
    CONF_PASSWORD,
    CONF_POINT,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.sse_energy.coordinator import SseEnergyCoordinator  # noqa: E402

_COORD = "custom_components.sse_energy.coordinator"

# today's date so the 15-min profile lands in the current month bucket
_TODAY = datetime.now(ZoneInfo("Europe/Bratislava")).date().isoformat()

USER_INFO = {
    "deliveryPoints": [
        {"id": "ELECTRICITY_X", "commodityType": "ELECTRICITY", "eic": "X", "name": "Dom", "city": "C"},
    ],
    "commodityTypes": ["ELECTRICITY"],
    "parameters": {"consumption": {"filterMinimalDateFrom": "2021-06-16"}},
}
DELIVERY_POINT = {
    "merchant": {
        "price": [{"value": 201.92, "remark1": "VT:"}, {"value": 107.33, "remark1": "NT:"}],
        "productName": {"value": "DD5"},
        "contractNumber": {"value": 123},
    },
    "distribution": {
        "distrTariff": {"value": "SSE_D5", "description": "D5"},
        "mainCircuitBreakerValue": {"value": "32.0"},
        "phaseNumber": {"value": 3},
        "distrCompany": {"value": "SSD"},
        "meteringDevice": [{"serialNumber": {"value": "100000"}}],
    },
}
DP_INFO = {
    "tariff": "DD5",
    "advancedPayment": {"amount": {"value": 190.0}, "frequency": "monthly"},
    "owedAmount": {"value": 190.0},
}
CONSUMPTION = {"consumption": [
    {"periodFrom": "2024-07-01", "periodTo": "2025-06-30",
     "electricity": {"lowTariff": {"value": 10.022}, "highTariff": {"value": 0.815}, "total": {"value": 10.837}},
     "fees": {"distribution": {"value": 560.18}, "powerEnergy": {"value": 815.34}, "totalSum": {"value": 1375.52}}},
]}
# hour 0 -> VT (preset 0,1,10,15), hour 5 -> NT
PROFILE = {"electricity": {"consumption": [
    {"date": _TODAY, "data": [
        {"periodFrom": "00:00", "value": 4.0},
        {"periodFrom": "05:00", "value": 8.0},
    ]},
], "preConsumption": []}}


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _mock_client(delivery_point=DELIVERY_POINT):
    c = MagicMock()
    c.expires_at = 0
    c.fetch_user_info.return_value = USER_INFO
    c.fetch_delivery_point.return_value = delivery_point
    c.fetch_delivery_point_info.return_value = DP_INFO
    c.fetch_consumption_summary.return_value = CONSUMPTION
    c.fetch_profile.return_value = PROFILE
    c.fetch_balance.return_value = {"outstanding": {"value": 190.0}, "excess": {"value": 0.0}}
    c.fetch_invoices.return_value = {"invoices": []}
    c.fetch_payments.return_value = {"paidSum": {"value": 1000.0}, "payments": []}
    c.fetch_meter_info.return_value = {"type": "TWO_RATE", "meters": [{"serialNumber": "100000"}]}
    c.fetch_outage.return_value = {"enabled": False, "active": False}
    return c


def _entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="ELECTRICITY_X",
        data={CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "x", CONF_POINT: "ELECTRICITY_X"},
    )
    entry.add_to_hass(hass)
    return entry


async def _run(hass, client):
    with patch(f"{_COORD}.SseClient", return_value=client), \
         patch(f"{_COORD}.async_import_statistics", new=AsyncMock(return_value=0)):
        coord = SseEnergyCoordinator(hass, _entry(hass))
        return await coord._async_update_data()


async def test_coordinator_builds_everything_from_api(hass: HomeAssistant):
    state = await _run(hass, _mock_client())

    # energy price = live contract price (EUR/MWh) / 1000 * default VAT 1.23
    assert state["price_vt"] == pytest.approx(0.2484, abs=1e-4)   # 201.92/1000*1.23
    assert state["price_nt"] == pytest.approx(0.1320, abs=1e-4)   # 107.33/1000*1.23
    # distribution calibrated from the official year: 560.18 / 10837
    assert state["distribution_rate_calibrated"] == pytest.approx(0.05169, abs=1e-5)
    # advance payment + contract surfaced from the API
    assert state["advance_amount"] == 190.0
    assert state["advance_frequency"] == "monthly"
    assert state["product"] == "DD5"
    assert state["distribution_tariff"] == "D5"
    # official billing year straight from the API
    assert state["year_consumption"] == 10837.0
    assert state["year_total_cost"] == 1375.52
    # VT-hour preset (0,1,10,15) resolved from tariff -> hour 0 = VT, hour 5 = NT
    assert state["month_vt"] == pytest.approx(1.0)   # roundkwh(4)
    assert state["month_nt"] == pytest.approx(2.0)   # roundkwh(8)
    assert state["month_consumption"] == pytest.approx(3.0)
    assert state["month_cost"] is not None


async def test_coordinator_no_prices_degrades_gracefully(hass: HomeAssistant):
    """API returns no energy prices -> cost is None but kWh are still counted, and the
    official yearly figures (independent of local pricing) are unaffected."""
    dp_no_price = {**DELIVERY_POINT, "merchant": {**DELIVERY_POINT["merchant"], "price": []}}
    state = await _run(hass, _mock_client(dp_no_price))

    assert state["price_vt"] is None
    assert state["price_nt"] is None
    assert state["month_cost"] is None
    assert state["month_consumption"] == pytest.approx(3.0)   # kWh still counted
    assert state["year_total_cost"] == 1375.52                # official year unaffected
    assert state["distribution_rate_calibrated"] == pytest.approx(0.05169, abs=1e-5)
