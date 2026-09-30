"""Hourly statistic series: DST handling, VT/NT cost split, revision window + running sum.

Loaded without importing Home Assistant (same synthetic-package trick as test_tariff).
"""
import importlib.util
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

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
    tariff = _load("tariff")
    return tariff, _load("series")


tariff, series = _load_pure()

TZ = "Europe/Bratislava"
T = tariff.TariffConfig(price_vt=0.20, price_nt=0.10, dist_rate=0.05, vt_hours=(0, 1, 10, 15))
UTC = timezone.utc


def _slots(hours, value=4.0):
    """Four 15-min slots of `value` kW (= value kWh per hour) for each wall-clock hour."""
    return [{"periodFrom": f"{h:02d}:{m:02d}", "value": value} for h in hours for m in (0, 15, 30, 45)]


def test_plain_day_vt_nt_and_cost_split():
    day = {"date": "2025-01-15", "data": _slots([0, 5])}
    out = series.build_series([day], T, TZ)
    h0 = datetime(2025, 1, 14, 23, tzinfo=UTC)  # 00:00 CET
    h5 = datetime(2025, 1, 15, 4, tzinfo=UTC)
    assert out["grid_consumption"] == {h0: 4.0, h5: 4.0}
    assert out["grid_consumption_vt"] == {h0: 4.0, h5: 0.0}
    assert out["grid_consumption_nt"] == {h0: 0.0, h5: 4.0}
    assert round(out["grid_cost_vt"][h0], 6) == round(4.0 * 0.25, 6)
    assert round(out["grid_cost_nt"][h5], 6) == round(4.0 * 0.15, 6)
    assert out["grid_cost_nt"][h0] == 0.0
    for h in (h0, h5):
        assert out["grid_cost"][h] == out["grid_cost_vt"][h] + out["grid_cost_nt"][h]


def test_no_cost_series_without_prices():
    day = {"date": "2025-01-15", "data": _slots([0])}
    out = series.build_series([day], tariff.TariffConfig(vt_hours=(0,)), TZ)
    assert set(out) == {"grid_consumption", "grid_consumption_vt", "grid_consumption_nt"}


def test_autumn_dst_doubled_hour_is_two_hours():
    # 2025-10-26: 02:00-02:59 happens twice; the portal repeats the same labels
    hours = [0, 1, 2, 2, 3] + list(range(4, 24))
    day = {"date": "2025-10-26", "data": _slots(hours)}
    cons = series.build_series([day], T, TZ)["grid_consumption"]
    assert len(cons) == 25
    assert cons[datetime(2025, 10, 26, 0, tzinfo=UTC)] == 4.0  # 02:00 CEST
    assert cons[datetime(2025, 10, 26, 1, tzinfo=UTC)] == 4.0  # 02:00 CET
    assert sum(cons.values()) == 100.0


def test_spring_dst_day_has_23_hours():
    hours = [0, 1] + list(range(3, 24))
    day = {"date": "2026-03-29", "data": _slots(hours)}
    cons = series.build_series([day], T, TZ)["grid_consumption"]
    assert len(cons) == 23
    assert sum(cons.values()) == 92.0


def test_cumulate_fills_missing_hours():
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    pts = {t0: 1.0, t0 + timedelta(hours=2): 2.0}
    rows = series.cumulate(pts, t0, t0 + timedelta(hours=3), 10.0)
    assert [s for _, s in rows] == [11.0, 11.0, 13.0, 13.0]


def test_revision_window():
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    pts = {t0 + timedelta(hours=h): 1.0 for h in range(48)}
    revise_from = t0 + timedelta(hours=24)
    last = t0 + timedelta(hours=40)
    # new series: everything
    assert series.revision_window(pts, None, revise_from) == (t0, t0 + timedelta(hours=47))
    # existing series: rewrite from revise_from
    assert series.revision_window(pts, last, revise_from) == (revise_from, t0 + timedelta(hours=47))
    # gap older than revise_from: start right after the last stored hour
    early = t0 + timedelta(hours=10)
    assert series.revision_window(pts, early, revise_from)[0] == t0 + timedelta(hours=11)
    # never before the data we have
    assert series.revision_window(pts, t0 - timedelta(days=5), revise_from)[0] == t0
    # stored hours beyond the data are zero-filled, keeping the sum continuous
    later = t0 + timedelta(hours=60)
    assert series.revision_window(pts, later, revise_from)[1] == later
    assert series.revision_window({}, last, revise_from) is None
