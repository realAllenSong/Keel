"""账单: the recurring series as a list, price changes, and "this is not a bill"."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Connection

from pf.ledger.importing import import_file
from pf.reports.common import Fx
from pf.reports.pulse import recurring_events
from pf.reports.recurring import bills, dismiss, restore

TODAY = dt.date(2026, 11, 20)


def _history(tmp: Path) -> Path:
    """A card with six months of a subscription (price up in November), a phone plan, an annual
    fee two years running, coffee on random days, and a checking account with rent and pay."""
    lines = []
    n = 0

    def add(day: str, amount: str, desc: str) -> None:
        nonlocal n
        n += 1
        lines.append({"id": f"l{n}", "date": day, "amount": amount, "description": desc})

    for m in range(6, 12):
        add(f"2026-{m:02d}-03", "-22.00" if m == 11 else "-20.00", "SUNO AI SUBSCRIPTION")
        add(f"2026-{m:02d}-12", "-45.50", "MINT MOBILE")
        for d in (2, 9, 17, 25):
            add(f"2026-{m:02d}-{d + m % 3:02d}", f"-{4 + d % 3}.25", "BLUE BOTTLE COFFEE")
    add("2026-09-28", "-21.60", "ANTHROPIC")  # began two months ago
    add("2026-10-28", "-21.60", "ANTHROPIC")
    add("2026-11-08", "-94.99", "ANTHROPIC")  # a one-off top-up beside it
    add("2025-10-05", "-95.00", "ANNUAL MEMBERSHIP FEE")
    add("2026-10-05", "-95.00", "ANNUAL MEMBERSHIP FEE")
    doc = {
        "keel_connector": 1, "fetched_at": "2026-11-20T12:00:00+00:00", "source": "plaid",
        "account_id": "card-1", "institution": "Testbank", "type": "Liabilities", "currency": "USD",
        "name": "Card", "mask": "1111", "asset_class": None, "balances": [], "valuations": [],
        "transactions": lines, "holdings": [],
    }  # fmt: skip
    p = tmp / "card.json"
    p.write_text(json.dumps(doc))
    return p


def test_bills_list_totals_price_change_and_dismissal(conn: Connection, tmp_path: Path) -> None:
    import_file(conn, _history(tmp_path))
    b = bills(conn, TODAY)
    names = {s.name: s for s in b.monthly}
    # Coffee is a habit, not a bill; two identical charges a month apart are a new subscription.
    assert set(names) == {"SUNO AI SUBSCRIPTION", "MINT MOBILE", "Anthropic"}
    young = names["Anthropic"]
    assert (young.new, young.amount, young.next_date, young.price_change) == (
        True,
        "21.60",
        "2026-11-28",
        None,
    )
    assert not names["MINT MOBILE"].new
    suno = names["SUNO AI SUBSCRIPTION"]
    assert (suno.amount, suno.day, suno.last_date, suno.next_date) == (
        "22.00",
        3,
        "2026-11-03",
        "2026-12-03",
    )
    assert suno.price_change is not None
    assert (suno.price_change.before, suno.price_change.now) == ("20.00", "22.00")
    mint = names["MINT MOBILE"]
    assert (mint.amount, mint.price_change, mint.next_date, mint.overdue_days) == (
        "45.50",
        None,
        "2026-12-12",
        None,
    )
    assert [(s.name, s.amount, s.month, s.next_date) for s in b.annual] == [
        ("ANNUAL MEMBERSHIP FEE", "95.00", 10, "2027-10-05")
    ]
    # 22 + 45.50 a month, plus a twelfth of 95.
    assert (b.monthly_total, b.yearly_total) == ("97.02", "1164.20")

    # "This is not a bill": gone from the list, from the forecast and from fixed spending.
    events, fixed = recurring_events(conn, TODAY, Fx(conn, "USD"))
    assert any(e.name == "MINT MOBILE" for e in events) and fixed
    dismiss(conn, mint.key, mint.name)
    b = bills(conn, TODAY)
    assert [s.name for s in b.monthly] == ["SUNO AI SUBSCRIPTION", "Anthropic"]
    assert [s.name for s in b.dismissed] == ["MINT MOBILE"]
    assert b.monthly_total == "51.52"
    events, fixed = recurring_events(conn, TODAY, Fx(conn, "USD"))
    assert not any(e.name == "MINT MOBILE" for e in events)
    assert all(amount != -4550 for _, _, amount in fixed)
    restore(conn, mint.key)
    assert len(bills(conn, TODAY).monthly) == 3
    # A new series is never forecast: the forecast keeps to what has proven itself.
    assert not any(e.name == "Anthropic" for e in events)


def _later(tmp: Path, day: str) -> Path:
    """One more sync of the same card: its data now reaches `day`."""
    doc = json.loads((tmp / "card.json").read_text())
    doc["transactions"] = [
        {"id": f"x{day}", "date": day, "amount": "-3.00", "description": "PARKING"}
    ]
    p = tmp / f"card-{day}.json"
    p.write_text(json.dumps(doc))
    return p


def test_overdue_needs_data_that_reaches_the_day_and_silence_means_stopped(
    conn: Connection, tmp_path: Path
) -> None:
    import_file(conn, _history(tmp_path))
    suno = lambda b: next(s for s in b.monthly if s.name.startswith("SUNO"))  # noqa: E731
    # Due on the 3rd, but the card's data ends in November: not late, just not in yet.
    assert suno(bills(conn, dt.date(2026, 12, 6))).overdue_days is None
    import_file(conn, _later(tmp_path, "2026-12-05"))
    late = suno(bills(conn, dt.date(2026, 12, 6)))  # the data passed the 3rd without it
    assert (late.next_date, late.overdue_days) == ("2026-12-03", 3)
    import_file(conn, _later(tmp_path, "2027-02-18"))
    assert bills(conn, dt.date(2027, 2, 20)).monthly == []  # months of silence: no longer a bill


def test_api_dismiss_and_restore(env: Path, tmp_path: Path) -> None:
    from pf.api.app import app
    from pf.db import make_engine

    with TestClient(app) as c:
        with make_engine().begin() as conn:
            import_file(conn, _history(tmp_path))
        listed = c.get("/api/bills").json()
        key = listed["annual"][0]["key"]
        after = c.post("/api/bills/dismiss", json={"key": key, "name": "fee"}).json()
        assert after["annual"] == [] and after["dismissed"][0]["key"] == key
        back = c.delete("/api/bills/dismiss", params={"key": key}).json()
        assert back["dismissed"] == [] and len(back["annual"]) == 1


def test_ach_names_lose_their_reference_numbers_and_the_holders_name() -> None:
    from pf.reports.recurring import short_name

    raw = "ACME CORP DES:PAYROLL ID:XXXXX8249401HWB INDN:DOE,JANE CO ID:XXXXX12345 PPD"
    assert short_name(raw) == "ACME CORP PAYROLL"
    assert short_name("SUNO AI SUBSCRIPTION") == "SUNO AI SUBSCRIPTION"


def test_a_fee_refunded_in_full_is_not_a_yearly_bill(conn: Connection, tmp_path: Path) -> None:
    doc = json.loads(_history(tmp_path).read_text())
    doc["transactions"] = [
        {
            "id": "f1",
            "date": "2025-09-01",
            "amount": "-795.00",
            "description": "ANNUAL MEMBERSHIP FEE",
        },
        {
            "id": "f2",
            "date": "2026-09-01",
            "amount": "-795.00",
            "description": "ANNUAL MEMBERSHIP FEE",
        },
        {"id": "f3", "date": "2026-09-04", "amount": "795.00", "description": "ANNUAL FEE REFUND"},
        {"id": "k1", "date": "2025-10-05", "amount": "-95.00", "description": "CLUB DUES"},
        {"id": "k2", "date": "2026-10-05", "amount": "-95.00", "description": "CLUB DUES"},
    ]
    p = tmp_path / "fees.json"
    p.write_text(json.dumps(doc))
    import_file(conn, p)
    b = bills(conn, TODAY)
    assert [s.name for s in b.annual] == ["CLUB DUES"]  # the refunded fee was not paid this year
    assert b.income == []  # and its refund is not income
