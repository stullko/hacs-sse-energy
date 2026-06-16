"""SSE eZona API client (sync; call via hass.async_add_executor_job).

Uses curl_cffi browser TLS/JA3/HTTP2 impersonation to pass the F5 BIG-IP bot
defense in front of ezona-zds.sse.sk. Bearer token is kept in memory for the
lifetime of the integration (re-login on HA restart is cheap).
"""
from __future__ import annotations

import logging
import secrets
import time

log = logging.getLogger(__name__)


class SseError(Exception):
    """Raised on login/data errors."""


class SseAuthError(SseError):
    """Raised specifically on invalid credentials."""


class SseClient:
    def __init__(
        self,
        username: str,
        password: str,
        point: str,
        base_url: str,
        auth_base_url: str,
        timeout: int = 30,
        impersonate: str = "chrome",
        token_buffer: int = 60,
    ):
        self._username = username
        self._password = password
        self._point = point
        self._base = base_url.rstrip("/")
        self._auth = auth_base_url.rstrip("/")
        self._timeout = timeout
        self._impersonate = impersonate
        self._buffer = token_buffer
        self.bearer = ""
        self.expires_at = 0
        self._session = self._new_session()

    def _new_session(self):
        from curl_cffi import requests as cffi  # imported lazily (HA installs it)

        return cffi.Session(impersonate=self._impersonate, timeout=self._timeout)

    def _headers(self, anonymous: bool = False) -> dict:
        h = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8",
            "Origin": "https://ezona.sse.sk",
            "Referer": "https://ezona.sse.sk/",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if not anonymous and self.bearer:
            h["Authorization"] = self.bearer
        return h

    def _send(self, method: str, url: str, json_body=None, anonymous: bool = False, attempts: int = 3):
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                resp = self._session.request(method, url, json=json_body, headers=self._headers(anonymous))
                code = resp.status_code
                if code in (401, 403):
                    raise SseAuthError(f"HTTP {code} for {url}")
                if code >= 400:
                    raise SseError(f"HTTP {code} for {url}")
                trimmed = (resp.text or "").lstrip()
                if not trimmed or trimmed[0] not in "{[":
                    snippet = " ".join(trimmed[:200].split())
                    raise SseError(f"Non-JSON response (WAF?) HTTP {code} for {url}: {snippet}")
                return resp.json()
            except SseAuthError:
                raise
            except SseError as e:
                last = e
                log.warning("request attempt %d/%d failed: %s", attempt, attempts, e)
                self._session = self._new_session()
                time.sleep(1.5 * attempt)
            except Exception as e:  # transport-level
                last = SseError(f"transport error for {url}: {e}")
                log.warning("request attempt %d/%d transport error: %s", attempt, attempts, e)
                self._session = self._new_session()
                time.sleep(1.5 * attempt)
        raise last if last else SseError(f"request failed for {url}")

    # ---- auth ----
    def login(self) -> str:
        if not self._username or not self._password:
            raise SseAuthError("credentials missing")
        self.bearer = ""
        self.expires_at = 0
        self._session = self._new_session()

        start = self._send("POST", f"{self._auth}/login/v1/start", {
            "clientId": "sseWebPortal",
            "redirectUri": "/",
            "responseType": "token",
            "scope": "CHANNEL_WEB AUTHTYPE_* PERM_* ROLE_*",
            "state": secrets.token_hex(10),
        }, anonymous=True)
        try:
            temp = start["temporaryAccessToken"]
            secret = start["nextStep"]["usernamePasswordMethod"]["secretPayload"]
        except (KeyError, TypeError) as err:
            raise SseError("login/v1/start did not return expected tokens") from err

        auth = self._send("POST", f"{self._auth}/login/v1/authenticate", {
            "authToken": {
                "usernamePasswordToken": {
                    "secretPayload": secret,
                    "username": self._username,
                    "password": self._password,
                }
            },
            "temporaryAccessToken": temp,
        }, anonymous=True)
        try:
            at = auth["accessToken"]
            bearer = f'{at["token_type"]} {at["access_token"]}'
        except (KeyError, TypeError) as err:
            raise SseAuthError("login/v1/authenticate returned no access token") from err

        expires_in = int(at.get("expires_in", 7199))
        self.bearer = bearer
        self.expires_at = int(time.time()) + expires_in
        log.info("SSE login OK; token valid ~%ds", expires_in)
        return bearer

    def _token_valid(self) -> bool:
        return bool(self.bearer) and time.time() < (self.expires_at - self._buffer)

    def ensure_login(self) -> None:
        if not self._token_valid():
            self.login()

    def _authed_get(self, url: str):
        self.ensure_login()
        try:
            return self._send("GET", url)
        except SseAuthError:
            log.info("auth error - re-logging in once")
            self.login()
            return self._send("GET", url)

    # ---- data ----
    def fetch_profile(self, period_from: str, period_to: str) -> dict:
        url = (
            f"{self._base}/delivery-point/v1/{self._point}/consumption/profile-measurement"
            f"?periodFrom={period_from}&periodTo={period_to}&losses=true"
        )
        return self._authed_get(url)

    def fetch_advance_payments(self, period_from: str, period_to: str) -> dict:
        url = (
            f"{self._base}/advance-payment/v1"
            f"?periodFrom={period_from}&periodTo={period_to}&commodityType=ELECTRICITY"
        )
        return self._authed_get(url)

    # ---- contract / billing / extras (discovered via portal recon) ----
    def fetch_user_info(self) -> dict:
        """Customer profile: lists ALL delivery points + data-history bounds.

        Not point-specific, so it can be called during config flow before a point
        is chosen (used to discover the user's delivery points)."""
        return self._authed_get(f"{self._base}/user/v1/info")

    def fetch_delivery_point(self) -> dict:
        """Full delivery-point detail: prices, product, distribution tariff, breaker."""
        return self._authed_get(f"{self._base}/delivery-point/v1/{self._point}")

    def fetch_delivery_point_info(self) -> dict:
        """Tariff + advance-payment summary (frequency/amount) for the current point."""
        return self._authed_get(f"{self._base}/delivery-point/v1/{self._point}/delivery-point-info")

    def fetch_consumption_summary(self) -> dict:
        """SSE's official per-billing-year totals (MWh) + fees (energy/distribution/total)."""
        return self._authed_get(f"{self._base}/delivery-point/v1/{self._point}/consumption")

    def fetch_balance(self) -> dict:
        return self._authed_get(f"{self._base}/invoice/v1/balance?commodityType=ELECTRICITY")

    def fetch_invoices(self, period_from: str, period_to: str) -> dict:
        url = (
            f"{self._base}/invoice/v1"
            f"?periodFrom={period_from}&periodTo={period_to}"
            f"&commodityType=ELECTRICITY&deliveryPointId={self._point}"
        )
        return self._authed_get(url)

    def fetch_payments(self, period_from: str, period_to: str) -> dict:
        url = (
            f"{self._base}/payment/v1"
            f"?periodFrom={period_from}&periodTo={period_to}&commodityType=ELECTRICITY"
        )
        return self._authed_get(url)

    def fetch_meter_info(self) -> dict:
        return self._authed_get(
            f"{self._base}/selfreport/v1/delivery-point/{self._point}/additional-info"
        )

    def fetch_outage(self) -> dict:
        """Portal-wide outage banner (anonymous endpoint)."""
        return self._send(
            "GET", f"{self._base}/anonymous/system-info/v1/outage", anonymous=True
        )
