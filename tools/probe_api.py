#!/usr/bin/env python3
"""Read-only probe of the SSE eZona API.

Goal: discover EVERY price / VAT / distribution-rate field the API actually
returns, so the integration can pull all rates live instead of hardcoding them.

Nothing is written to SSE - this only logs in and does GET requests. It reuses
the integration's own ``SseClient`` (so the curl_cffi browser-TLS impersonation
that gets past the F5 WAF is identical to production).

Usage (PowerShell):
    $env:SSE_USERNAME = "you@example.com"
    $env:SSE_PASSWORD = "your-password"      # optional; you'll be prompted if unset
    $env:SSE_POINT    = "ELECTRICITY_24ZSS0000000000X"   # optional; default below
    python tools\probe_api.py

Usage (bash):
    SSE_USERNAME=you@example.com python tools/probe_api.py

Output:
  * Full pretty JSON of the price-bearing endpoints is written to
    tools/sse_api_dump.json (UTF-8).
  * A focused list of "candidate" price/VAT/distribution fields is printed to
    the console so you can eyeball (and paste) just the relevant bits.

Privacy: the dump may contain your contract number, meter serial and POD number.
None of those are needed for the price work - feel free to redact them before
sharing the file.
"""
from __future__ import annotations

import getpass
import json
import os
import pathlib
import sys
from datetime import date, timedelta

# --- locate and import the integration's pure client (no Home Assistant deps) ---
_HERE = pathlib.Path(__file__).resolve().parent
_PKG = _HERE.parent / "custom_components" / "sse_energy"
sys.path.insert(0, str(_PKG))

try:
    from client import SseAuthError, SseClient, SseError  # type: ignore  # noqa: E402
except ImportError as err:  # pragma: no cover - import guard
    print(f"Could not import client.py from {_PKG}: {err}", file=sys.stderr)
    raise SystemExit(2) from err

# Defaults mirror const.py DEFAULTS (kept local so we don't import HA-coupled const.py).
BASE_URL = "https://ezona-zds.sse.sk/sse-gw/api/public"
AUTH_BASE_URL = "https://ezona-zds.sse.sk/sse-gw/auth/api/public"
DEFAULT_POINT = "ELECTRICITY_24ZSS0000000000X"

# keys whose name hints at a price / rate / tax / distribution component
_PRICE_KEY_HINTS = (
    "price", "cena", "sadzb", "vat", "dph", "tarif", "tariff", "distrib",
    "fee", "poplat", "rate", "unit", "mwh", "kwh", "remark", "amount", "sum",
)


_CAPTURE_WHOLE_MAXLEN = 300  # JSON length under which a hint-keyed block is shown whole


def _candidate_paths(obj, path: str = "") -> list[tuple[str, object]]:
    """Walk the JSON tree; return (dotted-path, value) for anything whose key looks
    price/VAT/distribution-related. A small hint-keyed block (e.g. the whole
    ``price[]`` list, or a ``{"value": .., "currency": ..}`` pair) is captured
    whole so the actual numbers are visible; large blocks are recursed into."""
    found: list[tuple[str, object]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            here = f"{path}.{key}" if path else str(key)
            is_hint = any(h in str(key).lower() for h in _PRICE_KEY_HINTS)
            if isinstance(val, (dict, list)):
                # show a small hint-keyed container whole (numbers included), else recurse
                if is_hint and len(json.dumps(val, ensure_ascii=False)) <= _CAPTURE_WHOLE_MAXLEN:
                    found.append((here, val))
                else:
                    found.extend(_candidate_paths(val, here))
            elif is_hint:
                found.append((here, val))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.extend(_candidate_paths(item, f"{path}[{i}]"))
    return found


def main() -> int:
    username = os.environ.get("SSE_USERNAME") or input("SSE username (email): ").strip()
    password = os.environ.get("SSE_PASSWORD") or getpass.getpass("SSE password: ")
    point = os.environ.get("SSE_POINT") or DEFAULT_POINT

    if not username or not password:
        print("username/password required (set SSE_USERNAME / SSE_PASSWORD)", file=sys.stderr)
        return 2

    try:
        client = SseClient(
            username=username,
            password=password,
            point=point,
            base_url=BASE_URL,
            auth_base_url=AUTH_BASE_URL,
        )
        print("logging in ...", file=sys.stderr)
        client.login()
        print("login OK", file=sys.stderr)
    except SseAuthError as err:
        print(f"AUTH FAILED (check credentials): {err}", file=sys.stderr)
        return 1
    except SseError as err:
        print(f"login error: {err}", file=sys.stderr)
        return 1

    today = date.today()
    win_from = (today - timedelta(days=3 * 365)).isoformat()
    win_to = today.isoformat()

    # Read-only GETs. The first two carry the prices we need; the rest are
    # included in case a fee/VAT breakdown lives there.
    probes = {
        "delivery_point": client.fetch_delivery_point,
        "consumption_summary": client.fetch_consumption_summary,
        "balance": client.fetch_balance,
        "invoices": lambda: client.fetch_invoices(win_from, win_to),
        "payments": lambda: client.fetch_payments(win_from, win_to),
        "meter_info": client.fetch_meter_info,
    }

    dump: dict[str, object] = {}
    for name, fn in probes.items():
        try:
            print(f"GET {name} ...", file=sys.stderr)
            dump[name] = fn()
        except Exception as err:  # noqa: BLE001 - diagnostic tool, capture everything
            dump[name] = {"__error__": str(err)}
            print(f"  {name} failed: {err}", file=sys.stderr)

    out_file = _HERE / "sse_api_dump.json"
    out_file.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFull JSON written to: {out_file}", file=sys.stderr)

    # focused candidate report
    print("\n=== candidate price / VAT / distribution fields ===")
    for name in ("delivery_point", "consumption_summary"):
        section = dump.get(name)
        if not isinstance(section, (dict, list)):
            continue
        print(f"\n[{name}]")
        for dotted, value in _candidate_paths(section):
            print(f"  {dotted} = {json.dumps(value, ensure_ascii=False)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
