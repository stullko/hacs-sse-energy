"""Verify the component's tariff math (loaded without importing Home Assistant).

custom_components/sse_energy/__init__.py imports homeassistant, so we load the pure
modules (models, tariff) directly under a synthetic package to avoid that.
"""
import importlib.util
import pathlib
import sys
import types

import pytest

_BASE = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "sse_energy"


def _load_pure():
    pkg = types.ModuleType("ssepure")
    pkg.__path__ = [str(_BASE)]
    sys.modules["ssepure"] = pkg

    def _load(name):
        spec = importlib.util.spec_from_file_location(f"ssepure.{name}", _BASE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"ssepure.{name}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("models")
    return _load("tariff")


tariff = _load_pure()

# Final (VAT-inclusive) EUR/kWh prices + calibrated distribution EUR/kWh, exactly
# as the coordinator builds them from the live API. tariff.py does no VAT math.
T = tariff.TariffConfig(
    price_vt=0.20,
    price_nt=0.10,
    dist_rate=0.05,
    vt_hours=(0, 1, 10, 15),
    currency="EUR",
)

DAY = {
    "date": "2025-01-15",
    "data": [
        {"periodFrom": "00:00", "periodTo": "00:15", "value": 4.0},
        {"periodFrom": "05:00", "periodTo": "05:15", "value": 8.0},
        {"periodFrom": "10:00", "periodTo": "10:15", "value": 4.0},
    ],
}
CONSUMPTION = {"electricity": {"consumption": [DAY]}}


def test_round_half_up():
    assert tariff.round_half_up(2.675, 2) == 2.68
    assert tariff.round_half_up(0.125, 2) == 0.13


def test_roundkwh():
    assert tariff.roundkwh(16.0) == 4.0
    assert tariff.roundkwh(10.0) == 2.5


def test_distribucia_calibrated():
    assert tariff.distribucia(2.0, 2.0, T) == pytest.approx(0.20, abs=1e-9)  # 0.05 * 4


def test_distribucia_none_without_rate():
    from dataclasses import replace
    assert tariff.distribucia(2.0, 2.0, replace(T, dist_rate=None)) is None


def test_compute_period():
    r = tariff.compute_period(CONSUMPTION, None, T, "2025-01-01", "2025-01-31")
    tt = r.totals
    assert tt.vt_kwh == pytest.approx(2.0, abs=1e-9)
    assert tt.nt_kwh == pytest.approx(2.0, abs=1e-9)
    assert tt.total_kwh == pytest.approx(4.0, abs=1e-9)
    assert tt.vt_eur == pytest.approx(0.40, abs=1e-9)   # 2.0 * 0.20
    assert tt.nt_eur == pytest.approx(0.20, abs=1e-9)   # 2.0 * 0.10
    assert tt.distribucia_eur == pytest.approx(0.20, abs=1e-9)
    assert tt.total_eur == pytest.approx(0.80, abs=1e-9)
    assert tt.balance_eur == pytest.approx(-0.80, abs=1e-9)


def test_compute_period_with_payments():
    payments = {"advancePayments": [{"status": "PAID", "totalAmount": {"value": 20.0}}]}
    r = tariff.compute_period(CONSUMPTION, payments, T, "2025-01-01", "2025-01-31")
    assert r.totals.paid_eur == pytest.approx(20.0, abs=1e-9)
    assert r.totals.balance_eur == pytest.approx(19.20, abs=1e-9)  # 20 - 0.80


def test_compute_period_no_prices_yields_kwh_only():
    """When the API hasn't supplied prices, kWh are still counted but cost is None."""
    from dataclasses import replace
    bare = replace(T, price_vt=None, price_nt=None, dist_rate=None)
    tt = tariff.compute_period(CONSUMPTION, None, bare, "2025-01-01", "2025-01-31").totals
    assert tt.total_kwh == pytest.approx(4.0, abs=1e-9)
    assert tt.vt_eur is None
    assert tt.nt_eur is None
    assert tt.distribucia_eur is None
    assert tt.total_eur is None
    assert tt.balance_eur is None


def test_hourly_energy():
    out = tariff.hourly_energy(DAY, T)
    assert out == [(0, 1.0, 1.0, 0.0), (5, 2.0, 0.0, 2.0), (10, 1.0, 1.0, 0.0)]
