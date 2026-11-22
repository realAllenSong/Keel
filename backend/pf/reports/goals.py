"""Savings goals: how far along, at what pace, and whether the date holds.

A goal counts the balance of the accounts linked to it (or net worth when none is linked), in
the goal's own currency. Pace is what those balances gained per month over the last three
months, so it follows what actually happened: deposits, interest, market moves. From the pace:
when the goal is reached at this rate, and what it takes each month to make the date."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection, text

from pf.reports.balance_sheet import balance_sheet
from pf.reports.common import Fx, cents

PACE_DAYS = 91


@dataclass
class Goal:
    id: int
    name: str
    target: str
    currency: str
    due: str | None
    accounts: list[str]  # [] = net worth
    saved: str
    progress: float  # 0…1 and beyond
    monthly_pace: str  # what the linked money gained per month lately (may be negative)
    reached: bool
    eta: str | None  # the month it is reached at this pace (YYYY-MM); None = not at this pace
    needed_per_month: str | None  # to make `due`; None without a date
    on_track: bool | None  # None without a date
    missing_fx: list[str]


def _held(conn: Connection, accounts: list[str], day: dt.date, fx: Fx) -> int:
    """Cents of the goal's currency in the linked accounts (or net worth) at the end of `day`."""
    sheet = balance_sheet(conn, day.isoformat(), fx)
    if not accounts:
        return sheet.net_worth
    wanted = set(accounts)
    return sum(line.balance for line in sheet.lines if line.account in wanted)


def _recorded(conn: Connection, accounts: list[str], since: dt.date, today: dt.date, fx: Fx) -> int:
    """What the counted accounts already held when they were first written into Keel after
    `since`: an opening balance, or the first value given to an account kept by its value."""
    rows = conn.execute(
        text(
            """
            SELECT vp.account, vp.date, vp.commodity, vp.amount FROM v_postings vp
              JOIN accounts a ON a.id = vp.account_id
             WHERE vp.kind IN ('opening', 'adjustment')
               AND a.type IN ('Assets', 'Liabilities') AND a.flow_only = 0
               AND vp.date > :s AND vp.date <= :e
               AND vp.date = (SELECT MIN(f.date) FROM v_postings f WHERE f.account_id = vp.account_id)
            """
        ),
        {"s": since.isoformat(), "e": today.isoformat()},
    ).all()
    wanted = set(accounts)
    return sum(
        fx.to_base(r.amount, r.commodity, r.date) for r in rows if not wanted or r.account in wanted
    )


def _goal(conn: Connection, r, today: dt.date) -> Goal:  # type: ignore[no-untyped-def]
    fx = Fx(conn, r.currency)
    accounts = json.loads(r.accounts or "[]")
    target = round(Decimal(r.target) * 100)
    now = _held(conn, accounts, today, fx)
    since = today - dt.timedelta(days=PACE_DAYS)
    before = _held(conn, accounts, since, fx)
    months = max((today - since).days / 30.44, 1)
    # An account first recorded inside the window brings its opening balance with it: that is
    # money you already had, not money saved lately.
    pace = round((now - before - _recorded(conn, accounts, since, today, fx)) / months)
    left = target - now
    reached = left <= 0
    eta = None
    if not reached and pace > 0:
        ahead = int(left / pace + 0.999)
        if ahead <= 600:
            y, m = divmod(today.year * 12 + today.month - 1 + ahead, 12)
            eta = f"{y:04d}-{m + 1:02d}"
    needed, on_track = None, None
    if r.due:
        due = dt.date.fromisoformat(r.due)
        months_left = max((due - today).days / 30.44, 0)
        if reached:
            needed, on_track = 0, True
        elif months_left <= 0:
            needed, on_track = left, False
        else:
            needed = round(left / months_left)
            on_track = pace >= needed
    return Goal(
        id=r.id,
        name=r.name,
        target=cents(target),
        currency=r.currency,
        due=r.due,
        accounts=accounts,
        saved=cents(now),
        progress=float(now / target) if target else 0.0,
        monthly_pace=cents(pace),
        reached=reached,
        eta=eta,
        needed_per_month=cents(needed) if needed is not None else None,
        on_track=on_track,
        missing_fx=sorted(fx.missing),
    )


def goals(conn: Connection, today: dt.date | None = None) -> list[Goal]:
    today = today or dt.date.today()
    rows = conn.execute(text("SELECT * FROM goals ORDER BY COALESCE(due, '9999'), id")).all()
    return [_goal(conn, r, today) for r in rows]


def _check(conn: Connection, target: Decimal, currency: str, accounts: list[str]) -> None:
    if target <= 0:
        raise ValueError("the target must be more than zero")
    if currency not in ("USD", "CNY"):
        raise ValueError("currency must be USD or CNY")
    known = {
        r.name
        for r in conn.execute(
            text("SELECT name FROM accounts WHERE type IN ('Assets', 'Liabilities')")
        )
    }
    if unknown := [a for a in accounts if a not in known]:
        raise ValueError(f"unknown account: {unknown[0]}")


def add(
    conn: Connection, name: str, target: Decimal, currency: str = "USD",
    due: dt.date | None = None, accounts: list[str] | None = None,
) -> int:  # fmt: skip
    accounts = accounts or []
    _check(conn, target, currency, accounts)
    return int(
        conn.execute(
            text("INSERT INTO goals(name, target, currency, due, accounts) VALUES (:n, :t, :c, :d, :a)"),
            {"n": name.strip()[:80] or "Goal", "t": str(target), "c": currency,
             "d": due.isoformat() if due else None, "a": json.dumps(accounts)},
        ).lastrowid  # type: ignore[arg-type]
    )  # fmt: skip


def update(
    conn: Connection, goal_id: int, name: str, target: Decimal, currency: str,
    due: dt.date | None, accounts: list[str],
) -> None:  # fmt: skip
    _check(conn, target, currency, accounts)
    conn.execute(
        text("UPDATE goals SET name = :n, target = :t, currency = :c, due = :d, accounts = :a WHERE id = :i"),
        {"i": goal_id, "n": name.strip()[:80] or "Goal", "t": str(target), "c": currency,
         "d": due.isoformat() if due else None, "a": json.dumps(accounts)},
    )  # fmt: skip


def delete(conn: Connection, goal_id: int) -> None:
    conn.execute(text("DELETE FROM goals WHERE id = :i"), {"i": goal_id})
