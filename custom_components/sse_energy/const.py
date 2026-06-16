"""Constants and defaults for the SSE Energy integration.

No prices or rates are hardcoded: energy prices, the distribution rate, the
delivery point and billing period are all loaded live from the API. The only
price-affecting value the API never returns is VAT, so it stays configurable;
VT/NT hour windows (also not in the API) are derived from a small verified preset
map keyed by the contract tariff, overridable in the options.
"""
from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME  # noqa: F401 (re-export)

DOMAIN = "sse_energy"
DEFAULT_NAME = "SSE Energy"
MANUFACTURER = "Stredoslovenska energetika"
MODEL = "eZona profile-measurement"

# config / option keys
CONF_POINT = "point"          # set per-entry from the picker; no hardcoded default
CONF_BASE_URL = "base_url"
CONF_AUTH_BASE_URL = "auth_base_url"
CONF_HTTP_TIMEOUT = "http_timeout"
CONF_IMPERSONATE = "impersonate"
CONF_TIMEZONE = "timezone"
CONF_VAT = "vat"
CONF_VT_HOURS = "vt_hours"
CONF_CURRENCY = "currency"
CONF_SCAN_INTERVAL = "scan_interval_seconds"
CONF_ENABLE_STATISTICS = "enable_statistics"
CONF_TOKEN_BUFFER = "token_expiry_buffer_seconds"

# Slovak standard VAT is 23% since 2025-01-01 (was 20%). VAT is the only
# price-affecting value the API does not return, so it stays configurable.
DEFAULT_VAT = 1.23

# VT/NT hour windows are not in the API and the 15-min profile carries no tariff
# band, so they're derived from the contract tariff/product via this preset map
# (verified values only). Unknown tariff -> empty window (all NT) + options override.
VT_HOURS_PRESETS: dict[str, tuple[int, ...]] = {
    # DD5 supply / D5 distribution: VT 00:00-02:00, 10:00-11:00, 15:00-16:00
    "DD5": (0, 1, 10, 15),
    "D5": (0, 1, 10, 15),
    "SSE_D5": (0, 1, 10, 15),
}


def vt_hours_for(tariff_or_product: str | None) -> tuple[int, ...] | None:
    """VT-hour preset for an API tariff/product code (case-insensitive).
    Returns None if unknown (caller then relies on a user-set override)."""
    if not tariff_or_product:
        return None
    return VT_HOURS_PRESETS.get(str(tariff_or_product).strip().upper())


DEFAULTS: dict = {
    CONF_BASE_URL: "https://ezona-zds.sse.sk/sse-gw/api/public",
    CONF_AUTH_BASE_URL: "https://ezona-zds.sse.sk/sse-gw/auth/api/public",
    CONF_HTTP_TIMEOUT: 30,
    CONF_IMPERSONATE: "chrome",
    CONF_TIMEZONE: "Europe/Bratislava",
    CONF_VAT: DEFAULT_VAT,
    CONF_CURRENCY: "EUR",
    CONF_SCAN_INTERVAL: 21600,
    CONF_ENABLE_STATISTICS: True,
    CONF_TOKEN_BUFFER: 60,
}
