"""账单: every recurring series in the ledger as a list a person can read and correct.

The series are the ones the forecast already finds (pf/reports/pulse.py): the same merchant on
about the same day in most recent months, or once a year. This module only shapes them: what
leaves every month no matter what, what each one costs, when it comes next, whether its price
changed, and which ones the user said are not bills (remembered, never suggested again).
Card payments and transfers between your own accounts are not costs and are left out."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import Connection, text

from pf.ledger.onboarding import coverage_through
from pf.reports.common import Fx, cents
from pf.reports.pulse import Pattern, recurring_events

SUBSCRIPTIONS = "Expenses:Subscriptions"


@dataclass
class PriceChange:
    before: str
    now: str
    since: str  # the day of the first charge at the new price


@dataclass
class Series:
    key: str
    name: str
    account: str
    category: str | None
    cadence: str  # monthly | annual
    day: int
    month: int | None  # annual: the month it falls in
    amount: str  # positive: what it costs (or brings in), in `currency`
    currency: str  # the statement's own currency
    estimated: bool
    last_date: str
    next_date: str | None
    overdue_days: int | None  # expected already, not seen yet
    count: int
    subscription: bool
    new: bool = False  # two charges so far: probably a subscription that just began
    price_change: PriceChange | None = None


@dataclass
class Bills:
    currency: str
    monthly_total: str  # every monthly bill, plus a twelfth of every annual one
    yearly_total: str
    monthly: list[Series] = field(default_factory=list)
    annual: list[Series] = field(default_factory=list)
    income: list[Series] = field(default_factory=list)
    dismissed: list[Series] = field(default_factory=list)
    missing_fx: list[str] = field(default_factory=list)


_ACH = re.compile(r"\s+DES:\s*(?P<what>[A-Za-z ]+?)\s+(?:ID|INDN|CO ID)\b.*$", re.I)


def short_name(name: str) -> str:
    """'ACME CORP DES:PAYROLL ID:XXXXX INDN:SMITH,J CO ID:...' -> 'ACME CORP PAYROLL':
    an ACH line's company and purpose, without the reference numbers and the holder's name."""
    m = _ACH.search(name)
    if not m:
        return name
    return f"{name[: m.start()].strip()} {m['what'].strip()}".strip()


def _series(p: Pattern, as_of: dt.date, covered: dt.date, prec: int) -> Series:
    """`covered`: the last day the account's data reaches. A charge expected after that day is
    not late, its statement is just not in yet."""

    def money(minor: int) -> str:
        return str((Decimal(abs(minor)) / (10**prec)).quantize(Decimal(1) / (10**prec)))

    change = None
    before = p.native_before
    if p.kind == "bill" and before:
        moved = abs(p.native_last - before)
        if max(50, abs(before) * 0.02) <= moved <= abs(before) * 0.5:
            change = PriceChange(
                before=money(before), now=money(p.native_last), since=p.last_date.isoformat()
            )
    overdue = None
    if p.next_date is not None and p.next_date <= min(as_of, covered):
        overdue = (as_of - p.next_date).days
    return Series(
        key=p.key,
        name=short_name(p.name),
        account=p.account,
        category=p.category,
        cadence=p.cadence,
        day=p.day,
        month=p.last_date.month if p.cadence == "annual" else None,
        # After a price change the last charge is the price; otherwise the usual amount.
        amount=money(p.native_last if change else p.native_amount),
        currency=p.commodity,
        estimated=p.estimated,
        last_date=p.last_date.isoformat(),
        next_date=p.next_date.isoformat() if p.next_date else None,
        overdue_days=overdue,
        count=p.count,
        subscription=(p.category or "").startswith(SUBSCRIPTIONS),
        new=p.tentative,
        price_change=change,
    )


def bills(conn: Connection, as_of: dt.date | None = None, currency: str = "USD") -> Bills:
    as_of = as_of or dt.date.today()
    fx = Fx(conn, currency)
    found: list[Pattern] = []
    recurring_events(conn, as_of, fx, patterns=found)
    out = Bills(currency=currency, monthly_total="0", yearly_total="0")
    prec = {
        r.code: r.precision for r in conn.execute(text("SELECT code, precision FROM commodities"))
    }
    covered: dict[str, dt.date] = {}
    monthly = yearly = 0
    for p in found:
        if p.kind not in ("bill", "income") or p.native_amount == 0:
            continue  # card payments and transfers move your own money
        category = p.category or ""
        if not category.startswith("Income:" if p.kind == "income" else "Expenses:"):
            continue  # a refund on a card is not income; an investment is not a bill
        if p.account not in covered:
            day = coverage_through(conn, p.account)
            covered[p.account] = dt.date.fromisoformat(day) if day else as_of
        current = covered[p.account] >= as_of - dt.timedelta(days=10)
        if p.next_date is None and current and not p.dismissed:
            continue  # stopped: long overdue on an account whose data is current
        s = _series(p, as_of, covered[p.account], prec.get(p.commodity, 2))
        in_base = fx.to_base(
            round(Decimal(s.amount) * 10 ** prec.get(p.commodity, 2)),
            p.commodity,
            as_of.isoformat(),
        )
        if p.dismissed:
            out.dismissed.append(s)
        elif p.kind == "income":
            out.income.append(s)
        elif p.cadence == "annual":
            out.annual.append(s)
            yearly += in_base
        else:
            out.monthly.append(s)
            monthly += in_base
    by_cost = lambda s: (-float(s.amount), s.name)  # noqa: E731
    for group in (out.monthly, out.annual, out.income, out.dismissed):
        group.sort(key=by_cost)
    out.monthly_total = cents(monthly + round(yearly / 12))
    out.yearly_total = cents(monthly * 12 + yearly)
    out.missing_fx = sorted(fx.missing)
    return out


def dismiss(conn: Connection, key: str, name: str = "") -> None:
    conn.execute(
        text("INSERT OR IGNORE INTO recurring_dismissed(key, name) VALUES (:k, :n)"),
        {"k": key, "n": name},
    )


def restore(conn: Connection, key: str) -> None:
    conn.execute(text("DELETE FROM recurring_dismissed WHERE key = :k"), {"k": key})
