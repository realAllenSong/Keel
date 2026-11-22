"""Closing a month: every account is in, balances agree, nothing waits for a decision.

Closing records what the month's books said (income and spending in the base currency). It does
not freeze the ledger: banks keep posting late lines dated into past months, and refusing them
would make the books wrong. Instead a closed month whose numbers later move is reported as
drift, so you see the change instead of it slipping in silently.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import Connection, text

from pf.ledger.onboarding import checklist
from pf.ledger.reconcile import reconcile
from pf.reports.cashflow import cashflow
from pf.reports.common import Fx, load_assumptions


@dataclass
class CloseCheck:
    id: str  # accounts | balances | review
    ok: bool
    count: int = 0  # how many things are still open
    items: list[str] = field(default_factory=list)  # what, for the detail line


@dataclass
class Closed:
    closed_at: str
    currency: str
    income: int
    expenses: int


@dataclass
class CloseStatus:
    month: str
    checks: list[CloseCheck]
    ready: bool
    closed: Closed | None
    income: int  # now, base-currency minor units
    expenses: int
    drift_income: int = 0  # now − at close
    drift_expenses: int = 0


def _month_end(month: str) -> str:
    y, m = (int(x) for x in month.split("-"))
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def _flows(conn: Connection, month: str, currency: str) -> tuple[int, int]:
    f = cashflow(conn, month, month, Fx(conn, currency))[0]
    return f.income, f.expenses


def close_status(conn: Connection, month: str) -> CloseStatus:
    end = _month_end(month)
    missing = [i for i in checklist(conn, month) if i.status != "ok"]
    gaps = [d for d in reconcile(conn) if d.date <= end]
    pending = conn.execute(
        text(
            "SELECT COUNT(*) FROM transactions WHERE status = 'pending' AND sealed = 1"
            " AND date BETWEEN :s AND :e"
        ),
        {"s": f"{month}-01", "e": end},
    ).scalar_one()
    checks = [
        CloseCheck(
            "accounts",
            not missing,
            len(missing),
            [i.institution or i.account.split(":")[1] for i in missing],
        ),
        CloseCheck("balances", not gaps, len(gaps), sorted({d.account for d in gaps})),
        CloseCheck("review", pending == 0, int(pending)),
    ]
    row = conn.execute(
        text("SELECT closed_at, currency, income, expenses FROM month_closes WHERE month = :m"),
        {"m": month},
    ).first()
    closed = Closed(row.closed_at, row.currency, row.income, row.expenses) if row else None
    currency = closed.currency if closed else load_assumptions().base_currency
    income, expenses = _flows(conn, month, currency)
    status = CloseStatus(
        month=month,
        checks=checks,
        ready=all(c.ok for c in checks),
        closed=closed,
        income=income,
        expenses=expenses,
    )
    if closed:
        status.drift_income = income - closed.income
        status.drift_expenses = expenses - closed.expenses
    return status


def close_month(conn: Connection, month: str, *, today: dt.date | None = None) -> CloseStatus:
    """Record the month as closed. Only a finished month whose checks all pass can close."""
    today = today or dt.date.today()
    if _month_end(month) >= today.isoformat():
        raise ValueError("month_not_over")
    status = close_status(conn, month)
    if not status.ready:
        raise ValueError("month_not_ready")
    if month in (held := _held(conn)):  # closed again by hand: back to normal
        _save_held(conn, [h for h in held if h != month])
    currency = load_assumptions().base_currency
    income, expenses = _flows(conn, month, currency)
    conn.execute(
        text(
            "INSERT INTO month_closes(month, currency, income, expenses) VALUES (:m, :c, :i, :e)"
            " ON CONFLICT(month) DO UPDATE SET closed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),"
            " currency = excluded.currency, income = excluded.income, expenses = excluded.expenses"
        ),
        {"m": month, "c": currency, "i": income, "e": expenses},
    )
    return close_status(conn, month)


def reopen_month(conn: Connection, month: str) -> None:
    """Open a closed month again. It stays open: closing by itself (see `auto_close`) leaves a
    month you reopened to you."""
    conn.execute(text("DELETE FROM month_closes WHERE month = :m"), {"m": month})
    held = _held(conn)
    if month not in held:
        _save_held(conn, [*held, month])


# ---------------------------------------------------------------- closing by itself
HELD_KEY = "close_held"  # months the user reopened: not closed automatically again
LOOK_BACK = 6  # finished months auto-close looks at


def _held(conn: Connection) -> list[str]:
    import json

    raw = conn.execute(
        text("SELECT value FROM app_settings WHERE key = :k"), {"k": HELD_KEY}
    ).scalar_one_or_none()
    return list(json.loads(raw)) if raw else []


def _save_held(conn: Connection, months: list[str]) -> None:
    import json

    conn.execute(
        text(
            "INSERT INTO app_settings(key, value) VALUES (:k, :v)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        ),
        {"k": HELD_KEY, "v": json.dumps(sorted(set(months)))},
    )


def auto_close(conn: Connection, today: dt.date | None = None) -> list[CloseStatus]:
    """Close every finished month that is ready and was never closed: all accounts in, balances
    agreeing, nothing waiting for a decision. That is all a person would check before pressing
    the button, so the button is pressed for them (Settings turns this off). A month the user
    reopened is left alone, and so is everything before the ledger's first month."""
    today = today or dt.date.today()
    first = conn.execute(
        text("SELECT MIN(substr(date, 1, 7)) FROM transactions WHERE kind != 'opening'")
    ).scalar()
    if not first:
        return []
    done = {r.month for r in conn.execute(text("SELECT month FROM month_closes"))}
    held = set(_held(conn))
    closed = []
    y, m = today.year, today.month
    for _ in range(LOOK_BACK):
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        month = f"{y:04d}-{m:02d}"
        if month < first:
            break
        if month in done or month in held:
            continue
        if close_status(conn, month).ready:
            closed.append(close_month(conn, month, today=today))
    return closed
