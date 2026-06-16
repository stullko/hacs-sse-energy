"""Verify portal.py parsers against the real JSON shapes captured from the portal."""
import importlib.util
import pathlib
import sys
import types

_BASE = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "sse_energy"


def _load(name):
    pkg = sys.modules.get("ssepure2")
    if pkg is None:
        pkg = types.ModuleType("ssepure2")
        pkg.__path__ = [str(_BASE)]
        sys.modules["ssepure2"] = pkg
    spec = importlib.util.spec_from_file_location(f"ssepure2.{name}", _BASE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"ssepure2.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


portal = _load("portal")

DELIVERY_POINT = {
    "general": {"podNumber": {"value": 7654321}},
    "merchant": {
        "invoiceCycle": {"value": "Jún"},
        "productName": {"description": "DD5", "value": "DD5"},
        "price": [
            {"value": 201.92, "currency": "EUR", "remark1": "VT:", "remark2": "/MWh"},
            {"value": 107.33, "currency": "EUR", "remark1": "NT:", "remark2": "/MWh"},
        ],
        "contractNumber": {"value": 9100000000},
    },
    "distribution": {
        "voltageLevel": {"value": "low_voltage"},
        "mainCircuitBreakerValue": {"value": "32.0000000"},
        "phaseNumber": {"value": 3},
        "distrCompany": {"value": "Stredoslovenská distribučná, a.s."},
        "distrTariff": {"description": "D5", "value": "SSE_D5"},
        "meteringDevice": [{"serialNumber": {"value": "100000"}}],
    },
}

CONSUMPTION = {"consumption": [
    {"periodFrom": "2023-07-01", "periodTo": "2024-06-30",
     "electricity": {"lowTariff": {"value": 9.279}, "highTariff": {"value": 0.799}, "total": {"value": 10.078}},
     "fees": {"distribution": {"value": 496.81}, "powerEnergy": {"value": 763.63}, "totalSum": {"value": 1260.44}}},
    {"periodFrom": "2024-07-01", "periodTo": "2025-06-30",
     "electricity": {"lowTariff": {"value": 10.022}, "highTariff": {"value": 0.815}, "total": {"value": 10.837}},
     "fees": {"distribution": {"value": 560.18}, "powerEnergy": {"value": 815.34}, "totalSum": {"value": 1375.52}}},
]}

# Real shape captured from GET /user/v1/info (personal data trimmed).
USER_INFO = {
    "deliveryPoints": [
        {"id": "ELECTRICITY_24ZSS0000000000X", "commodityType": "ELECTRICITY",
         "name": "Mesto, Ulica", "street": "Ulica", "city": "Mesto",
         "state": "ACTIVE", "outage": "YES", "distribution": "SSD",
         "eic": "24ZSS0000000000X"},
        {"id": "GAS_SKSPPDIS00012345", "commodityType": "GAS",
         "name": "Mesto, Ulica", "city": "Mesto", "state": "ACTIVE",
         "eic": "SKSPPDIS00012345"},
    ],
    "commodityTypes": ["ELECTRICITY", "GAS"],
    "parameters": {"consumption": {"filterDefaultDateFrom": "2021-06-16",
                                   "filterMinimalDateFrom": "2021-06-16"}},
}

# Real shape captured from GET /delivery-point/v1/{id}/delivery-point-info.
DP_INFO = {
    "deliveryPointId": "ELECTRICITY_24ZSS0000000000X", "commodityType": "ELECTRICITY",
    "owedAmount": {"value": 190.0, "currency": "EUR"},
    "advancedPayment": {"amount": {"value": 190.0, "currency": "EUR"}, "frequency": "monthly"},
    "tariff": "DD5",
}


def test_parse_delivery_point():
    c = portal.parse_delivery_point(DELIVERY_POINT)
    assert c.price_vt_mwh == 201.92
    assert c.price_nt_mwh == 107.33
    assert c.product == "DD5"
    assert c.distr_tariff == "D5"
    assert c.breaker_a == 32.0
    assert c.phases == 3
    assert c.meter_serial == "100000"
    assert c.contract_number == "9100000000"
    assert c.invoice_cycle == "Jún"


def test_parse_consumption_summary_latest():
    years = portal.parse_consumption_summary(CONSUMPTION)
    assert len(years) == 2
    y = portal.latest_year(years)
    assert y.period_to == "2025-06-30"
    assert y.nt_kwh == 10022.0
    assert y.vt_kwh == 815.0
    assert y.total_kwh == 10837.0
    assert y.energy_eur == 815.34
    assert y.distribution_eur == 560.18
    assert y.total_eur == 1375.52


def test_parse_balance():
    b = portal.parse_balance({"outstanding": {"value": 190.0}, "excess": {"value": 0.0}})
    assert b["outstanding"] == 190.0
    assert b["excess"] == 0.0


def test_parse_invoices_settlement():
    inv = {"invoices": [
        {"type": "ELECTRICITY_ADVANCES_BREAKDOWN", "dateTo": "2026-06-30",
         "totalAmount": {"value": 950.0}, "dueDate": "2026-06-15", "paid": False},
        {"type": "ELECTRICITY_SETTLEMENT_INVOICE", "dateFrom": "2024-07-01", "dateTo": "2025-06-30",
         "totalAmount": {"value": 147.28}, "dueDate": "2025-07-21", "paid": True, "variableSymbol": 9000000000,
         "balanceType": "OUTSTANDING_PAYMENTS"},
    ]}
    s = portal.parse_invoices(inv)["settlement"]
    assert s["amount"] == 147.28
    assert s["period_to"] == "2025-06-30"
    assert s["paid"] is True
    assert s["status"] == "Nedoplatok"


def test_classify_balance():
    assert portal.classify_balance(190.0, 0.0) == ("Nedoplatok", -190.0)
    assert portal.classify_balance(0.0, 42.5) == ("Preplatok", 42.5)
    assert portal.classify_balance(0.0, 0.0) == ("Vyrovnané", 0.0)


def test_current_billing_period():
    from datetime import date
    assert portal.current_billing_period("2025-06-30", date(2026, 6, 16)) == ("2025-07-01", "2026-06-30")
    # fallback when no completed year known (today in June -> previous July)
    assert portal.current_billing_period(None, date(2026, 6, 16)) == ("2025-07-01", "2026-06-30")


def test_sum_advances_since():
    p = {"payments": [
        {"date": "2026-05-15", "amount": {"value": 190.0}, "relatedInvoice": [{"type": "ELECTRICITY_ADVANCE"}]},
        {"date": "2025-08-15", "amount": {"value": 156.0}, "relatedInvoice": [{"type": "ELECTRICITY_ADVANCE"}]},
        {"date": "2025-07-21", "amount": {"value": 147.28}, "relatedInvoice": [{"type": "ELECTRICITY_SETTLEMENT_INVOICE"}]},
        {"date": "2024-01-01", "amount": {"value": 99.0}, "relatedInvoice": [{"type": "ELECTRICITY_ADVANCE"}]},
    ]}
    # since 2025-07-01: 190 + 156 (advances); settlement and the old 2024 payment excluded
    assert portal.sum_advances_since(p, "2025-07-01") == 346.0


def test_status_from_net():
    assert portal.status_from_net(5.0) == "Preplatok"
    assert portal.status_from_net(-5.0) == "Nedoplatok"
    assert portal.status_from_net(0.0) == "Vyrovnané"


def test_parse_payments_last():
    p = {"paidSum": {"value": 4851.81}, "overPaymentSum": {"value": 44.22},
         "payments": [{"date": "2026-05-15", "amount": {"value": 190.0}, "method": "Inkaso"}]}
    r = portal.parse_payments(p)
    assert r["paid_sum"] == 4851.81
    assert r["last"]["amount"] == 190.0
    assert r["last"]["date"] == "2026-05-15"


def test_parse_meter_and_outage():
    m = portal.parse_meter_info({"type": "TWO_RATE", "meters": [{"serialNumber": "100000"}]})
    assert m["meter_type"] == "TWO_RATE"
    assert m["serial"] == "100000"
    o = portal.parse_outage({"enabled": True, "active": False, "title": "x", "message": "y"})
    assert o["enabled"] is True
    assert o["active"] is False


def test_parse_user_info():
    u = portal.parse_user_info(USER_INFO)
    assert [p.id for p in u.delivery_points] == [
        "ELECTRICITY_24ZSS0000000000X", "GAS_SKSPPDIS00012345"
    ]
    dp = u.delivery_points[0]
    assert dp.eic == "24ZSS0000000000X"
    assert dp.city == "Mesto"
    assert dp.commodity == "ELECTRICITY"
    assert dp.label  # human-friendly label for the selector
    assert u.commodity_types == ["ELECTRICITY", "GAS"]
    assert u.min_date_from == "2021-06-16"
    # convenience: only the electricity points
    assert [p.id for p in u.electricity_points] == ["ELECTRICITY_24ZSS0000000000X"]


def test_parse_user_info_empty():
    u = portal.parse_user_info({})
    assert u.delivery_points == []
    assert u.commodity_types == []
    assert u.min_date_from is None


def test_parse_delivery_point_info():
    i = portal.parse_delivery_point_info(DP_INFO)
    assert i.tariff == "DD5"
    assert i.advance_amount == 190.0
    assert i.advance_frequency == "monthly"
    assert i.owed_amount == 190.0


def test_parse_delivery_point_info_empty():
    i = portal.parse_delivery_point_info({})
    assert i.tariff is None
    assert i.advance_amount is None
