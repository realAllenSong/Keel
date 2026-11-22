"""Savings goals: progress from the linked accounts, pace from the last months, the date."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection

from pf.ledger.core import ensure_account
from pf.ledger.edits import add_manual
from pf.reports import goals as g

TODAY = dt.date(2026, 11, 20)
SAVINGS, CHECKING = "Assets:Bank:Savings", "Assets:Bank:Checking"


def _money(conn: Connection) -> None:
    for a in (SAVINGS, CHECKING):
        ensure_account(conn, a, currency="USD", asset_class="cash")
    put = lambda day, account, amount: add_manual(  # noqa: E731
        conn, date=dt.date.fromisoformat(day), account=account, amount=Decimal(amount),
        category="Income:Salary", description="in")  # fmt: skip
    put("2026-06-01", SAVINGS, "4000")  # before the pace window
    for day in ("2026-09-01", "2026-10-01", "2026-11-01"):
        put(day, SAVINGS, "1000")  # a thousand a month lately
    put("2026-11-05", CHECKING, "2500")


def test_progress_pace_and_date(conn: Connection) -> None:
    _money(conn)
    gid = g.add(conn, "Emergency fund", Decimal("10000"), "USD", dt.date(2027, 2, 20), [SAVINGS])
    goal = g.goals(conn, TODAY)[0]
    assert (goal.id, goal.saved, goal.progress, goal.reached) == (gid, "7000.00", 0.7, False)
    # 3000 over about three months of window: a thousand a month; 3000 left -> three more months.
    assert 990 <= float(goal.monthly_pace) <= 1020
    assert goal.eta in ("2027-02", "2027-03")
    assert 980 <= float(goal.needed_per_month or 0) <= 1010 and goal.on_track is not None

    # A date that the pace cannot make, and one already reached.
    g.update(conn, gid, "Emergency fund", Decimal("20000"), "USD", dt.date(2027, 1, 20), [SAVINGS])
    tight = g.goals(conn, TODAY)[0]
    assert tight.on_track is False and float(tight.needed_per_month or 0) > 6000
    g.update(conn, gid, "Emergency fund", Decimal("5000"), "USD", None, [SAVINGS])
    done = g.goals(conn, TODAY)[0]
    assert (done.reached, done.eta, done.needed_per_month, done.on_track) == (
        True,
        None,
        None,
        None,
    )


def test_no_accounts_means_net_worth_and_bad_input_is_refused(conn: Connection) -> None:
    _money(conn)
    g.add(conn, "First 100k", Decimal("100000"))
    goal = g.goals(conn, TODAY)[0]
    assert (goal.accounts, goal.saved) == ([], "9500.00")
    with pytest.raises(ValueError, match="unknown account"):
        g.add(conn, "x", Decimal("10"), accounts=["Assets:Nope"])
    with pytest.raises(ValueError, match="more than zero"):
        g.add(conn, "x", Decimal("0"))
    g.delete(conn, goal.id)
    assert g.goals(conn, TODAY) == []


def test_api(env: Path) -> None:
    from pf.api.app import app
    from pf.db import make_engine

    with TestClient(app) as c:
        with make_engine().begin() as conn:
            _money(conn)
        made = c.post(
            "/api/goals",
            json={
                "name": "Trip",
                "target": "3000",
                "currency": "USD",
                "due": "2027-06-30",
                "accounts": [CHECKING],
            },
        ).json()
        assert [(x["name"], x["saved"]) for x in made] == [("Trip", "2500.00")]
        gid = made[0]["id"]
        assert (
            c.put(
                f"/api/goals/{gid}",
                json={
                    "name": "Trip",
                    "target": "2000",
                    "currency": "USD",
                    "due": None,
                    "accounts": [CHECKING],
                },
            ).json()[0]["reached"]
            is True
        )
        assert c.post("/api/goals", json={"name": "bad", "target": "-1"}).status_code == 422
        assert c.delete(f"/api/goals/{gid}").json() == []


def test_an_account_recorded_lately_is_not_money_saved_lately(conn: Connection) -> None:
    from pf.ledger.valuation import set_value

    _money(conn)
    g.add(conn, "First 100k", Decimal("100000"))
    before = float(g.goals(conn, TODAY)[0].monthly_pace)
    # A deposit held for years, written into Keel this month.
    ensure_account(conn, "Assets:Oldbank:Deposit", currency="USD", asset_class="term_deposit")
    set_value(conn, "Assets:Oldbank:Deposit", dt.date(2026, 11, 8), Decimal("40000"))
    after = g.goals(conn, TODAY)[0]
    assert after.saved == "49500.00"  # it counts toward the goal
    assert abs(float(after.monthly_pace) - before) < 1  # and does not look like a month's saving
