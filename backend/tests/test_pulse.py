"""The month in progress: recurring-event detection, forecast accuracy and alerts, evaluated on
the synthetic demo household and on small generated scenarios."""

from __future__ import annotations

import csv
import datetime as dt
import statistics
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection

from pf.ledger.decisions import import_decisions
from pf.ledger.importing import import_file
from pf.prefs import Prefs, load_prefs, save_prefs
from pf.reports.cashflow import cashflow
from pf.reports.pulse import cash_history, pulse

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "fixtures" / "demo"


@pytest.fixture
def demo(conn: Connection) -> Connection:
    import yaml

    for f in sorted((DEMO / "statements").iterdir()):
        import_file(conn, f, archive=False)
    import_decisions(conn, yaml.safe_load((DEMO / "decisions.yaml").read_text()))
    return conn


def _upcoming(conn: Connection, day: dt.date) -> set[tuple[str, str, int]]:
    p = pulse(conn, day)
    assert p is not None
    return {(e.kind, e.name, e.date.day) for e in p.upcoming if e.date.month == 11}


def test_live_pulse_uses_today_even_when_last_import_is_in_previous_month(
    demo: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NextMonth(dt.date):
        @classmethod
        def today(cls) -> dt.date:
            return cls(2026, 11, 1)

    monkeypatch.setattr("pf.reports.pulse.dt.date", NextMonth)
    p = pulse(demo)
    assert p is not None
    assert p.as_of.isoformat() == "2026-11-01"
    assert p.month == "2026-11"
    assert p.spent == 0


def test_calendar_finds_the_households_fixed_events_and_nothing_else(demo: Connection) -> None:
    # fixtures/demo/generate.py: payroll on the 1st and 15th, rent (Zelle) on the 1st, PSE&G on
    # the 7th, Verizon the 12th, Vanguard the 20th, interest the 28th, Spotify the 3rd, Netflix
    # the 9th, Claude the 18th; card autopays on the 22nd and 25th. Groceries, fuel, dining and
    # Amazon happen on random days and must not become "bills".
    found = _upcoming(demo, dt.date(2026, 10, 15))
    names = {(k, d) for k, _, d in found}
    assert ("income", 1) in names and ("income", 15) in names
    assert {("bill", d) for d in (1, 3, 7, 9, 12)} <= names
    assert not [
        n for _, n, _ in found if any(w in n for w in ("Whole Foods", "Shell", "Trader", "Amazon"))
    ]
    p = pulse(demo, dt.date(2026, 10, 15))
    assert p is not None
    october = {(e.kind, e.date.day) for e in p.upcoming if e.date.month == 10}
    assert ("card_payment", 22) in october and ("card_payment", 25) in october
    assert ("transfer", 20) in october  # Vanguard: categorised to an asset, so not spending
    # The card payment is predicted from what is owed on the card today.
    autopay = next(e for e in p.upcoming if e.kind == "card_payment" and e.date.day == 25)
    assert autopay.estimated and autopay.counterparty == "Liabilities:Chase:Freedom"


def test_netflix_price_is_the_latest_one(demo: Connection) -> None:
    p = pulse(demo, dt.date(2026, 10, 15))
    assert p is not None
    netflix = next(e for e in p.upcoming if e.name == "Netflix")
    assert netflix.amount == -1799  # raised from 15.49 in 2025-09


def test_forecast_beats_a_typical_month_and_sharpens_as_the_month_goes(demo: Connection) -> None:
    """Eval: forecast month-end spending on days 5/10/20/25 of Jan–Sep 2026 and compare with what
    the month actually spent (cash-flow statement)."""
    err: dict[int, list[float]] = {d: [] for d in (5, 10, 20, 25)}
    base: list[float] = []
    for m in range(1, 10):
        actual = cashflow(demo, f"2026-{m:02d}", f"2026-{m:02d}")[0].expenses
        for d in err:
            p = pulse(demo, dt.date(2026, m, d))
            assert p is not None
            err[d].append(abs(p.forecast - actual) / actual)
            if d == 5:
                base.append(abs(p.typical_month - actual) / actual)
    mape = {d: statistics.mean(v) for d, v in err.items()}
    assert mape[10] < statistics.mean(base) and mape[20] < statistics.mean(base)
    assert mape[25] <= mape[10] and mape[20] < 0.07


def test_curve_and_budget(demo: Connection) -> None:
    p = pulse(demo, dt.date(2026, 10, 15))
    assert p is not None
    assert len(p.curve) == 31 and p.curve[14].actual == p.spent and p.curve[15].actual is None
    assert all(c.low <= c.typical <= c.high for c in p.curve)
    assert p.budget_source == "typical" and p.budget == p.typical_month
    save_prefs(demo, Prefs(monthly_budget="3000"))
    p = pulse(demo, dt.date(2026, 10, 15))
    assert p is not None and p.budget == 300_000 and p.budget_source == "set"
    assert any(a.id == "over-budget-2026-10" for a in p.alerts)  # 3,561 already spent


# ---------------------------------------------------------------- generated scenarios
def _checking(path: Path, rows: list[tuple[dt.date, str, Decimal]], opening: Decimal) -> Path:
    """A Chase checking export (newest first, running balance) for Assets:Chase:Checking."""
    bal, out = opening, []
    for d, desc, amt in sorted(rows):
        bal += amt
        out.append(
            [
                "CREDIT" if amt > 0 else "DEBIT",
                d.strftime("%m/%d/%Y"),
                desc,
                f"{amt:.2f}",
                "ACH",
                f"{bal:.2f}",
                "",
            ]
        )
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "Details",
                "Posting Date",
                "Description",
                "Amount",
                "Type",
                "Balance",
                "Check or Slip #",
            ]
        )
        w.writerows(reversed(out))
    return path


def _months(n: int, last: dt.date) -> list[dt.date]:
    out, d = [], last.replace(day=1)
    for _ in range(n):
        out.append(d)
        d = (d - dt.timedelta(days=1)).replace(day=1)
    return out[::-1]


def test_missing_paycheck_and_low_cash(conn: Connection, tmp_path: Path) -> None:
    rows: list[tuple[dt.date, str, Decimal]] = []
    for m in _months(6, dt.date(2026, 9, 1)):
        rows.append((m.replace(day=2), "ACME CORP PAYROLL PPD ID: 9000000001", Decimal("5000.00")))
        rows.append((m.replace(day=3), "ZELLE PAYMENT TO OAKWOOD APARTMENTS", Decimal("-3000.00")))
        rows.append((m.replace(day=12), "VERIZON WIRELESS PAYMENTS", Decimal("-85.00")))
        rows.append((m.replace(day=20), "COSTCO WHSE #0001", Decimal("-1900.00")))
    rows.append((dt.date(2026, 10, 1), "SWEETGREEN NEW YORK", Decimal("-15.00")))
    import_file(
        conn,
        _checking(tmp_path / "Chase0001_Activity_a.CSV", rows, Decimal("600.00")),
        archive=False,
    )
    p = pulse(conn, dt.date(2026, 10, 9))  # payday (2nd) passed a week ago without pay
    assert p is not None
    ids = {a.id.split("-2026")[0] for a in p.alerts}
    assert "missing-income-ACME CORP PAYROLL" in {a.id.rsplit("-", 3)[0] for a in p.alerts}
    # Rent is due (overdue on the 3rd) and the paycheck has not arrived: cash goes below the floor.
    assert "low-cash" in ids
    low = next(a for a in p.alerts if a.id.startswith("low-cash"))
    assert "$500" in low.body


def test_alerts_are_notified_once(demo: Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    import pf.ops.alerts as alerts

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(alerts, "notify", lambda title, body: sent.append((title, body)))
    monkeypatch.setattr(alerts, "pulse", lambda conn: pulse(conn, dt.date(2026, 10, 31)))
    save_prefs(demo, Prefs(monthly_budget="3000", language="en"))
    first = alerts.notify_new_alerts(demo)
    assert first and sent and sent[0][0] == "Over budget this month"
    assert alerts.notify_new_alerts(demo) == []  # already told
    save_prefs(demo, load_prefs(demo).model_copy(update={"alerts": False}))
    demo.execute(__import__("sqlalchemy").text("DELETE FROM app_settings WHERE key='alerts_seen'"))
    assert alerts.notify_new_alerts(demo) == []  # turned off in Settings


def test_pulse_api_speaks_the_requested_language(env: Path) -> None:
    from pf.api.app import app

    with TestClient(app) as c:
        statements = sorted((DEMO / "statements").iterdir())[-6:]
        c.post("/api/import", files=[("files", (f.name, f.read_bytes())) for f in statements])
        assert (
            c.put("/api/settings", json={"monthly_budget": "100"}).json()["monthly_budget"] == "100"
        )
        zh = c.get(
            "/api/pulse", params={"as_of": "2026-10-31"}, headers={"Accept-Language": "zh"}
        ).json()
        en = c.get(
            "/api/pulse",
            params={"as_of": "2026-10-31"},
            headers={"Accept-Language": "en-US,en;q=0.9"},
        ).json()
    assert zh["has_data"] and zh["budget_source"] == "set"
    assert zh["alerts"][0]["title"] == "本月已超出预算"
    assert en["alerts"][0]["title"] == "Over budget this month"


def test_settings_roundtrip_and_validation(env: Path) -> None:
    from pf.api.app import app

    with TestClient(app) as c:
        assert c.get("/api/settings").json()["language"] == "system"
        r = c.put("/api/settings", json={"language": "en", "theme": "dark", "low_balance": "800"})
        assert r.json()["theme"] == "dark" and r.json()["low_balance"] == "800"
        assert c.put("/api/settings", json={"low_balance": "-5"}).status_code == 422
        assert c.get("/api/settings").json()["language"] == "en"


def test_cash_history_walks_the_cash_account_and_counts_real_flows_only(demo: Connection) -> None:
    days = cash_history(demo, dt.date(2026, 9, 1), dt.date(2026, 10, 15))
    assert len(days) == 45
    p = pulse(demo, dt.date(2026, 10, 15))
    assert p is not None
    assert days[-1].balance == p.cash_now  # ends where the pulse says cash is today
    sept = [d for d in days if d.date.month == 9]
    flow = cashflow(demo, "2026-09", "2026-09")[0]
    assert sum(d.inflow for d in sept) == flow.income
    assert sum(d.outflow for d in sept) == flow.expenses
    assert {d.date.day for d in sept if d.inflow > 100_000} == {1, 15}  # the two paydays
