"""HA config + options flow tests (pytest-homeassistant-custom-component).

Covers the new fully-dynamic flow: log in -> discover delivery points from
/user/v1/info -> auto-pick (1) or select (many); plus the trimmed options flow.
The SseClient is mocked, so no network / curl_cffi is touched.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# These tests need Home Assistant, which only imports on Linux (fcntl/resource).
# On a host without the HA test harness (e.g. native Windows) the whole module is
# skipped so the pure-logic tests still run. Run these in WSL/Docker/CI.
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.sse_energy.client import SseAuthError, SseError
from custom_components.sse_energy.const import (
    CONF_CURRENCY,
    CONF_ENABLE_STATISTICS,
    CONF_PASSWORD,
    CONF_POINT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VAT,
    CONF_VT_HOURS,
    DOMAIN,
)

_CF = "custom_components.sse_energy.config_flow.SseClient"


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    """Custom integration must be loadable for these tests (needs the hass fixture)."""
    yield


ONE = {
    "deliveryPoints": [
        {"id": "ELECTRICITY_24ZSS0000000000X", "commodityType": "ELECTRICITY",
         "name": "Mesto, Ulica", "city": "Mesto", "eic": "24ZSS0000000000X", "state": "ACTIVE"},
    ],
    "commodityTypes": ["ELECTRICITY"],
    "parameters": {"consumption": {"filterMinimalDateFrom": "2021-06-16"}},
}
TWO = {
    "deliveryPoints": [
        {"id": "ELECTRICITY_AAA", "commodityType": "ELECTRICITY", "name": "Dom A", "city": "A", "eic": "AAA"},
        {"id": "ELECTRICITY_BBB", "commodityType": "ELECTRICITY", "name": "Dom B", "city": "B", "eic": "BBB"},
    ],
    "commodityTypes": ["ELECTRICITY"],
}
GAS_ONLY = {
    "deliveryPoints": [{"id": "GAS_X", "commodityType": "GAS", "name": "Plyn", "city": "X", "eic": "X"}],
    "commodityTypes": ["GAS"],
}


def _mock_client(user_info, login_exc=None):
    client = MagicMock()
    if login_exc is not None:
        client.login.side_effect = login_exc
    client.fetch_user_info.return_value = user_info
    return client


async def test_user_single_point_creates_entry(hass: HomeAssistant):
    with patch(_CF, return_value=_mock_client(ONE)):
        res = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert res["type"] == FlowResultType.FORM
        assert res["step_id"] == "user"
        res = await hass.config_entries.flow.async_configure(
            res["flow_id"], {CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "x"}
        )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_POINT] == "ELECTRICITY_24ZSS0000000000X"
    assert res["result"].unique_id == "ELECTRICITY_24ZSS0000000000X"


async def test_user_multiple_points_then_select(hass: HomeAssistant):
    with patch(_CF, return_value=_mock_client(TWO)):
        res = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        res = await hass.config_entries.flow.async_configure(
            res["flow_id"], {CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "x"}
        )
        assert res["type"] == FlowResultType.FORM
        assert res["step_id"] == "select_point"
        res = await hass.config_entries.flow.async_configure(
            res["flow_id"], {CONF_POINT: "ELECTRICITY_BBB"}
        )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_POINT] == "ELECTRICITY_BBB"


async def test_invalid_auth(hass: HomeAssistant):
    with patch(_CF, return_value=_mock_client(ONE, login_exc=SseAuthError("bad creds"))):
        res = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        res = await hass.config_entries.flow.async_configure(
            res["flow_id"], {CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "bad"}
        )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass: HomeAssistant):
    with patch(_CF, return_value=_mock_client(ONE, login_exc=SseError("waf"))):
        res = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        res = await hass.config_entries.flow.async_configure(
            res["flow_id"], {CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "x"}
        )
    assert res["errors"] == {"base": "cannot_connect"}


async def test_no_electricity_points(hass: HomeAssistant):
    with patch(_CF, return_value=_mock_client(GAS_ONLY)):
        res = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        res = await hass.config_entries.flow.async_configure(
            res["flow_id"], {CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "x"}
        )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"base": "no_points"}


async def test_duplicate_point_aborts(hass: HomeAssistant):
    MockConfigEntry(
        domain=DOMAIN, unique_id="ELECTRICITY_24ZSS0000000000X",
        data={CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "x", CONF_POINT: "ELECTRICITY_24ZSS0000000000X"},
    ).add_to_hass(hass)
    with patch(_CF, return_value=_mock_client(ONE)):
        res = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        res = await hass.config_entries.flow.async_configure(
            res["flow_id"], {CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "x"}
        )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "already_configured"


async def test_options_flow_drops_empty_vt_hours(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="ELECTRICITY_X",
        data={CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "x", CONF_POINT: "ELECTRICITY_X"},
    )
    entry.add_to_hass(hass)
    # OptionsFlowWithReload schedules an entry reload on submit; neutralize it so the
    # test exercises only the options-flow result (not a full coordinator/network setup).
    with patch("homeassistant.config_entries.ConfigEntries.async_reload", new=AsyncMock()):
        res = await hass.config_entries.options.async_init(entry.entry_id)
        assert res["type"] == FlowResultType.FORM
        assert res["step_id"] == "init"
        res = await hass.config_entries.options.async_configure(
            res["flow_id"],
            {CONF_VAT: 1.23, CONF_VT_HOURS: "", CONF_CURRENCY: "EUR",
             CONF_SCAN_INTERVAL: 21600, CONF_ENABLE_STATISTICS: True},
        )
        await hass.async_block_till_done()
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_VAT] == 1.23
    assert CONF_VT_HOURS not in res["data"]  # empty -> coordinator uses tariff preset


async def test_options_flow_keeps_vt_hours_override(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="ELECTRICITY_X",
        data={CONF_USERNAME: "a@b.sk", CONF_PASSWORD: "x", CONF_POINT: "ELECTRICITY_X"},
    )
    entry.add_to_hass(hass)
    with patch("homeassistant.config_entries.ConfigEntries.async_reload", new=AsyncMock()):
        res = await hass.config_entries.options.async_init(entry.entry_id)
        res = await hass.config_entries.options.async_configure(
            res["flow_id"],
            {CONF_VAT: 1.23, CONF_VT_HOURS: "0,1,10,15", CONF_CURRENCY: "EUR",
             CONF_SCAN_INTERVAL: 21600, CONF_ENABLE_STATISTICS: True},
        )
        await hass.async_block_till_done()
    assert res["data"][CONF_VT_HOURS] == "0,1,10,15"
