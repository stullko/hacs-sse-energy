#!/usr/bin/env python3
"""Read-only exploration probe for making the integration fully dynamic (multi-user).

Answers two questions the static dump could not:
  1. Does a 15-min profile slot carry a VT/NT tariff band? (If yes, we can split
     VT/NT per-tariff dynamically instead of hardcoding DD5 hours.)
  2. Is there an endpoint that LISTS a customer's delivery points / EANs, so the
     config flow can discover them instead of asking the user to type an EAN?

Only logs in and does GETs. Reuses the integration's SseClient.

    $env:SSE_USERNAME="you@example.com"; $env:SSE_PASSWORD="..."; python tools\probe_explore.py
"""
from __future__ import annotations

import getpass
import json
import os
import pathlib
import sys
from datetime import date, timedelta

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "custom_components" / "sse_energy"))
from client import SseAuthError, SseClient, SseError  # type: ignore  # noqa: E402

BASE = "https://ezona-zds.sse.sk/sse-gw/api/public"
AUTH = "https://ezona-zds.sse.sk/sse-gw/auth/api/public"
POINT = os.environ.get("SSE_POINT") or "ELECTRICITY_24ZSS0000000000X"

# candidate "list my delivery points / account" endpoints to discover
CANDIDATES = [
    f"{BASE}/delivery-point/v1",
    f"{BASE}/delivery-point/v1/list",
    f"{BASE}/delivery-point/v1/all",
    f"{BASE}/delivery-point/v1/summary",
    f"{BASE}/customer/v1",
    f"{BASE}/customer/v1/me",
    f"{BASE}/customer/v1/delivery-points",
    f"{BASE}/account/v1",
    f"{BASE}/account/v1/me",
    f"{BASE}/user/v1/me",
    f"{BASE}/profile/v1",
    f"{BASE}/contract/v1",
    f"{BASE}/business-partner/v1",
    f"{BASE}/delivery-point/v1/{POINT}/tariff",
    f"{BASE}/delivery-point/v1/{POINT}/prices",
    f"{AUTH}/userinfo/v1",
    f"{AUTH}/user/v1/me",
]


def _probe_url(client, url: str) -> dict:
    """Raw GET via the client session so we can see the real status code."""
    try:
        resp = client._session.request("GET", url, headers=client._headers())
        text = (resp.text or "").strip()
        shape = None
        if resp.status_code == 200 and text[:1] in "[{":
            try:
                j = resp.json()
                shape = ["list", len(j)] if isinstance(j, list) else ["dict", list(j.keys())]
            except Exception:  # noqa: BLE001
                shape = ["?", "non-json"]
        return {"status": resp.status_code, "shape": shape, "preview": " ".join(text[:200].split())}
    except Exception as err:  # noqa: BLE001
        return {"error": str(err)}


def main() -> int:
    username = os.environ.get("SSE_USERNAME") or input("SSE username (email): ").strip()
    password = os.environ.get("SSE_PASSWORD") or getpass.getpass("SSE password: ")

    client = SseClient(username=username, password=password, point=POINT, base_url=BASE, auth_base_url=AUTH)
    try:
        print("logging in ...", file=sys.stderr)
        client.login()
        print("login OK\n", file=sys.stderr)
    except (SseAuthError, SseError) as err:
        print(f"login failed: {err}", file=sys.stderr)
        return 1

    out: dict = {}

    # --- Q1: profile slot structure (does a slot carry a tariff band?) ---
    today = date.today()
    pf = (today - timedelta(days=5)).isoformat()
    pt = today.isoformat()
    print(f"=== Q1: profile slots {pf}..{pt} ===")
    try:
        prof = client.fetch_profile(pf, pt)
        out["profile_sample"] = prof
        el = (prof or {}).get("electricity") or {}
        print("electricity keys:", list(el.keys()))
        days = el.get("consumption") or el.get("preConsumption") or []
        if days:
            day0 = days[0]
            print("day[0] keys:", list(day0.keys()), "| date:", day0.get("date"))
            slots = day0.get("data") or []
            print(f"day[0] has {len(slots)} slots; first 3 raw slots (ALL keys):")
            for s in slots[:3]:
                print("   ", json.dumps(s, ensure_ascii=False))
            # union of keys across all slots of day0 (in case a band field is sparse)
            allkeys = set()
            for s in slots:
                allkeys |= set(s.keys())
            print("union of slot keys in day[0]:", sorted(allkeys))
        else:
            print("no consumption/preConsumption days returned in this window")
    except Exception as err:  # noqa: BLE001
        print("profile fetch failed:", err)
        out["profile_error"] = str(err)

    # --- Q2: discover a delivery-point LIST / account endpoint ---
    print("\n=== Q2: candidate list/account endpoints ===")
    disc = {}
    for url in CANDIDATES:
        r = _probe_url(client, url)
        disc[url] = r
        tag = url.replace(BASE, "{api}").replace(AUTH, "{auth}")
        print(f"  {r.get('status', 'ERR'):>5}  {tag}")
        if r.get("shape"):
            print(f"         shape={r['shape']}")
        elif r.get("status") == 200 and r.get("preview"):
            print(f"         preview={r['preview'][:120]}")
    out["endpoint_discovery"] = disc

    dump = _HERE / "sse_explore_dump.json"
    dump.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nfull JSON -> {dump}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
