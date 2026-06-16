"""Config + options + reauth flows for SSE Energy.

The delivery point is discovered from the API (/user/v1/info) and chosen from a
list - no EAN is typed or hardcoded. Options expose only the values the API does
not return (VAT, VT/NT hours) plus currency / interval / statistics toggle.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.core import callback

from . import portal
from .client import SseAuthError, SseClient, SseError
from .const import (
    CONF_AUTH_BASE_URL,
    CONF_BASE_URL,
    CONF_CURRENCY,
    CONF_ENABLE_STATISTICS,
    CONF_PASSWORD,
    CONF_POINT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VAT,
    CONF_VT_HOURS,
    DEFAULTS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _client(username: str, password: str, point: str = "") -> SseClient:
    return SseClient(
        username=username,
        password=password,
        point=point,
        base_url=DEFAULTS[CONF_BASE_URL],
        auth_base_url=DEFAULTS[CONF_AUTH_BASE_URL],
    )


async def _login_and_points(hass, username: str, password: str) -> list[portal.DeliveryPointRef]:
    """Log in and return the account's electricity delivery points.

    Raises SseAuthError / SseError on failure."""
    client = _client(username, password)

    def _work():
        client.login()
        return client.fetch_user_info()

    info = await hass.async_add_executor_job(_work)
    return portal.parse_user_info(info or {}).electricity_points


async def _validate_login(hass, data: dict) -> None:
    """Try to log in (used by reauth); raises SseAuthError / SseError on failure."""
    client = _client(data[CONF_USERNAME], data[CONF_PASSWORD], data.get(CONF_POINT, ""))
    await hass.async_add_executor_job(client.login)


class SseEnergyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup: credentials -> discover -> pick delivery point."""

    VERSION = 1

    def __init__(self) -> None:
        self._creds: dict[str, str] = {}
        self._points: list[portal.DeliveryPointRef] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                points = await _login_and_points(
                    self.hass, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except SseAuthError:
                errors["base"] = "invalid_auth"
            except SseError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error during setup")
                errors["base"] = "unknown"
            else:
                if not points:
                    errors["base"] = "no_points"
                else:
                    self._creds = {
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    }
                    self._points = points
                    if len(points) == 1:
                        return await self._create_entry(points[0].id)
                    return await self.async_step_select_point()

        schema = vol.Schema({
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_select_point(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return await self._create_entry(user_input[CONF_POINT])
        choices = {p.id: p.label for p in self._points}
        schema = vol.Schema({vol.Required(CONF_POINT): vol.In(choices)})
        return self.async_show_form(step_id="select_point", data_schema=schema)

    async def _create_entry(self, point_id: str):
        await self.async_set_unique_id(point_id)
        self._abort_if_unique_id_configured()
        ref = next((p for p in self._points if p.id == point_id), None)
        title = f"SSE {ref.label}" if ref else f"SSE {point_id}"
        return self.async_create_entry(title=title, data={**self._creds, CONF_POINT: point_id})

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await _validate_login(self.hass, {**entry.data, **user_input})
            except SseAuthError:
                errors["base"] = "invalid_auth"
            except SseError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)

        schema = vol.Schema({
            vol.Required(CONF_USERNAME, default=entry.data.get(CONF_USERNAME)): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SseEnergyOptionsFlow()


class SseEnergyOptionsFlow(OptionsFlowWithReload):
    """Only the values the API doesn't return (VAT, VT/NT hours) + currency/interval.

    Subclasses OptionsFlowWithReload so HA reloads the entry when options change
    (replaces the deprecated add_update_listener + async_reload pattern)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            # empty vt_hours -> drop it so the coordinator uses the tariff preset
            cleaned = {
                k: v for k, v in user_input.items()
                if not (k == CONF_VT_HOURS and (v is None or v == ""))
            }
            return self.async_create_entry(title="", data=cleaned)

        cur = {**DEFAULTS, **self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema({
            vol.Required(CONF_VAT, default=cur[CONF_VAT]): vol.Coerce(float),
            vol.Optional(CONF_VT_HOURS, default=cur.get(CONF_VT_HOURS, "")): str,
            vol.Required(CONF_CURRENCY, default=cur[CONF_CURRENCY]): str,
            vol.Required(CONF_SCAN_INTERVAL, default=cur[CONF_SCAN_INTERVAL]): vol.Coerce(int),
            vol.Required(CONF_ENABLE_STATISTICS, default=cur[CONF_ENABLE_STATISTICS]): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
