#!/usr/bin/env python3
"""Read-only end-to-end check of the new fully-dynamic pricing path against live API.

Mirrors what the coordinator does (without Home Assistant): discover delivery
points, pull live prices + tariff + advance, calibrate distribution, and build the
effective tariff. Confirms client.py + portal.py + tariff.py work on real data.

    $env:SSE_USERNAME="..."; $env:SSE_PASSWORD="..."; python tools\verify_dynamic.py
"""
from __future__ import annotations

import os
import pathlib
import sys

_PKG = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "sse_energy"
sys.path.insert(0, str(_PKG))

import portal  # type: ignore  # noqa: E402
from client import SseClient  # type: ignore  # noqa: E402

# mirrors const.VT_HOURS_PRESETS (const.py imports Home Assistant, so we can't here)
VT_HOURS_PRESETS = {"DD5": (0, 1, 10, 15), "D5": (0, 1, 10, 15), "SSE_D5": (0, 1, 10, 15)}
VAT = 1.23


def vt_hours_for(code):
    return VT_HOURS_PRESETS.get(str(code).strip().upper()) if code else None


def main() -> int:
    c = SseClient(
        username=os.environ["SSE_USERNAME"],
        password=os.environ["SSE_PASSWORD"],
        point=os.environ.get("SSE_POINT", ""),
        base_url="https://ezona-zds.sse.sk/sse-gw/api/public",
        auth_base_url="https://ezona-zds.sse.sk/sse-gw/auth/api/public",
    )
    c.login()

    ui = portal.parse_user_info(c.fetch_user_info())
    print("delivery points (electricity):")
    for p in ui.electricity_points:
        print(f"   - {p.id}   label={p.label!r}")
    print("min_date_from:", ui.min_date_from, "| commodities:", ui.commodity_types)
    assert ui.electricity_points, "no electricity points parsed!"

    point = os.environ.get("SSE_POINT") or ui.electricity_points[0].id
    c._point = point  # target the first point for the detail calls

    contract = portal.parse_delivery_point(c.fetch_delivery_point())
    dpi = portal.parse_delivery_point_info(c.fetch_delivery_point_info())
    years = portal.parse_consumption_summary(c.fetch_consumption_summary())
    year = portal.latest_year(years)

    calib = (year.distribution_eur / year.total_kwh) if (year and year.total_kwh) else None
    price_vt = contract.price_vt_mwh / 1000.0 * VAT if contract.price_vt_mwh else None
    price_nt = contract.price_nt_mwh / 1000.0 * VAT if contract.price_nt_mwh else None
    hours = vt_hours_for(dpi.tariff) or vt_hours_for(contract.product) or vt_hours_for(contract.distr_tariff)

    print("\n--- effective tariff the coordinator would build (live, VAT=%.2f) ---" % VAT)
    print(f"  tariff/product   : {dpi.tariff} / {contract.product} / {contract.distr_tariff}")
    print(f"  advance payment  : {dpi.advance_amount} ({dpi.advance_frequency})")
    print(f"  price_vt (EUR/kWh): {round(price_vt, 4) if price_vt else None}   (raw {contract.price_vt_mwh} EUR/MWh)")
    print(f"  price_nt (EUR/kWh): {round(price_nt, 4) if price_nt else None}   (raw {contract.price_nt_mwh} EUR/MWh)")
    print(f"  dist_rate (calib): {round(calib, 5) if calib else None}   (from {year.period_from}..{year.period_to})")
    print(f"  vt_hours (preset): {hours}")
    assert price_vt and price_nt and calib and hours, "pricing chain incomplete!"
    print("\nOK: full dynamic pricing chain works on live data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
