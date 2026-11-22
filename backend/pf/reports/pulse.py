"""The month in progress, day by day: how fast money is going out compared with a usual month,
where the month will likely end, what is still due (a financial calendar learned from history)
and whether the cash accounts stay above a comfortable floor until then.

Everything is derived from the ledger; nothing is configured except the optional budget and the
low-balance floor in Settings.

Recurring events: a merchant (merchant_key of the statement description) that shows up on about
the same day of the month (±3 days) in at least 3 of the last 6 months is a monthly event; its
amount is the median of the last three. Card payments are recurring transfers whose amount is
predicted from the card's current balance rather than history (it changes every month). A charge
seen twice about a year apart is an annual event.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from pf.i18n import tr
from pf.ledger.categorize import merchant_key
from pf.prefs import load_prefs
from pf.reports.common import Fx, add_months, load_assumptions, month_end, month_start

_NOISE = re.compile(r"\s+(PPD|WEB|CCD)\s+ID:?.*$", re.I)
HISTORY_MONTHS = 6
HORIZON_DAYS = 35
DAY_TOLERANCE = 3


@dataclass
class CurvePoint:
    day: int
    actual: int | None  # cumulative cents spent by the end of this day (None = future)
    typical: int
    low: int
    high: int


@dataclass
class Event:
    date: dt.date
    name: str
    amount: int  # cents, ledger sign of the account: negative = money out
    kind: str  # bill | income | card_payment | transfer
    account: str
    cadence: str  # monthly | annual
    category: str | None = None
    estimated: bool = False  # amount predicted (varies month to month)
    overdue: bool = False  # expected on `date`, not seen yet
    counterparty: str | None = None  # transfers: the other account (e.g. the card being paid)


@dataclass
class CashPoint:
    date: dt.date
    balance: int


@dataclass
class Alert:
    id: str  # stable: notifications are sent once per id
    severity: str  # warning | notice | info
    title: str
    body: str
    href: str | None = None


@dataclass
class Pulse:
    as_of: dt.date
    month: str
    days_in_month: int
    spent: int
    typical_to_date: int
    typical_month: int
    budget: int
    budget_source: str  # set | typical
    forecast: int
    remaining_bills: int
    safe_per_day: int | None
    curve: list[CurvePoint]
    upcoming: list[Event]
    cash_now: int
    cash_low: int
    cash_low_date: dt.date | None
    low_balance: int
    cash_series: list[CashPoint]
    alerts: list[Alert] = field(default_factory=list)
    history_months: int = 0


def latest_date(conn: Connection, today: dt.date | None = None) -> dt.date | None:
    today = today or dt.date.today()
    d = conn.execute(
        text(
            "SELECT MAX(date) FROM transactions WHERE sealed = 1 AND kind IN ('normal', 'transfer')"
            " AND date <= :t"
        ),
        {"t": today.isoformat()},
    ).scalar()
    return dt.date.fromisoformat(d) if d else None


# ---------------------------------------------------------------- spending curve
def _daily_spend(conn: Connection, start: dt.date, end: dt.date, fx: Fx) -> dict[dt.date, int]:
    rows = conn.execute(
        text(
            "SELECT date, commodity, SUM(amount) AS amt FROM v_postings"
            " WHERE account_type = 'Expenses' AND kind NOT IN ('adjustment', 'opening')"
            " AND date BETWEEN :s AND :e GROUP BY date, commodity"
        ),
        {"s": start.isoformat(), "e": end.isoformat()},
    ).all()
    out: defaultdict[dt.date, int] = defaultdict(int)
    for r in rows:
        out[dt.date.fromisoformat(r.date)] += fx.to_base(r.amt, r.commodity, r.date)
    return out


def _cumulative(daily: dict[dt.date, int], month: str, upto: int) -> list[int]:
    first = month_start(month)
    last_day = month_end(month).day
    run, out = 0, []
    for d in range(1, upto + 1):
        if d <= last_day:
            run += daily.get(first.replace(day=d), 0)
        out.append(run)
    return out


def _band(values: list[int]) -> tuple[int, int, int]:
    """(low, typical, high): quartiles with 4+ months, else min / median / max."""
    if len(values) >= 4:
        q = statistics.quantiles(values, n=4, method="inclusive")
        return int(q[0]), int(statistics.median(values)), int(q[2])
    return min(values), int(statistics.median(values)), max(values)


# ---------------------------------------------------------------- recurring events
@dataclass
class _Hit:
    date: dt.date
    amount: int
    account: str
    account_type: str
    asset_class: str | None
    category: str | None
    kind: str
    other_type: str | None  # transfer: type of the other statement account
    other_account: str | None
    name: str
    native: int = 0  # minor units of `commodity`, as the statement has it
    commodity: str = "USD"
    wallet: bool = False  # a flow-only account (Alipay, WeChat): not part of the cash forecast


def _hits(
    conn: Connection, start: dt.date, end: dt.date, fx: Fx, *, wallets: bool = False
) -> list[_Hit]:
    rows = conn.execute(
        text(
            """
            SELECT s.txn_id, s.date, s.description, s.payee, s.kind, s.account, s.account_type,
                   s.asset_class, s.amount, s.commodity, a.flow_only,
                   (SELECT o.account FROM v_postings o WHERE o.txn_id = s.txn_id AND o.id != s.id
                     ORDER BY o.id LIMIT 1) AS other,
                   (SELECT o.account_type FROM v_postings o WHERE o.txn_id = s.txn_id
                     AND o.id != s.id ORDER BY o.id LIMIT 1) AS other_type
            FROM v_postings s JOIN accounts a ON a.id = s.account_id
            WHERE s.source_key IS NOT NULL AND s.account_type IN ('Assets', 'Liabilities')
              AND (a.flow_only = 0 OR :wallets) AND s.kind IN ('normal', 'transfer')
              AND s.date BETWEEN :s AND :e
            """
        ),
        {"s": start.isoformat(), "e": end.isoformat(), "wallets": 1 if wallets else 0},
    ).all()
    out = []
    for r in rows:
        category = r.other if r.kind == "normal" else None
        out.append(
            _Hit(
                date=dt.date.fromisoformat(r.date),
                amount=fx.to_base(r.amount, r.commodity, r.date),
                account=r.account,
                account_type=r.account_type,
                asset_class=r.asset_class,
                category=category,
                kind=r.kind,
                other_type=r.other_type if r.kind == "transfer" else None,
                other_account=r.other if r.kind == "transfer" else None,
                # Paired transfers carry a synthetic payee ("Transfer → …"); the bank's text is
                # the recognisable name.
                name=_NOISE.sub(
                    "", r.description if r.kind == "transfer" else (r.payee or r.description)
                ).strip(),
                native=r.amount,
                commodity=r.commodity,
                wallet=bool(r.flow_only),
            )
        )
    return out


_REFUND = re.compile(r"refund|reversal|reversed|returned|退款|退回|冲正", re.I)
REFUND_DAYS = 45


def _unrefunded(hits: list[_Hit]) -> list[_Hit]:
    """Hits without the charges that were given back in full: an annual fee refunded three days
    later was not paid, so it is no evidence of a yearly bill (and its refund is not income).
    A refund is a credit of the same amount on the same account within a few weeks that says it
    is one, or that comes from the same merchant."""
    gone: set[int] = set()
    credits = [h for h in hits if h.native > 0 and h.kind == "normal"]
    for c in credits:
        says_so = bool(_REFUND.search(c.name))
        for h in hits:
            if (
                id(h) in gone
                or h.kind != "normal"
                or h.account != c.account
                or h.commodity != c.commodity
                or h.native != -c.native
                or not 0 <= (c.date - h.date).days <= REFUND_DAYS
            ):
                continue
            if says_so or merchant_key(h.name) == merchant_key(c.name):
                gone.update((id(h), id(c)))
                break
    return [h for h in hits if id(h) not in gone]


def _day_gap(a: int, b: int) -> int:
    d = abs(a - b)
    return min(d, 31 - d)


def _clusters(hits: list[_Hit]) -> list[list[_Hit]]:
    """Group a merchant's hits by day of month (±DAY_TOLERANCE, wrapping at month end)."""
    groups: list[list[_Hit]] = []
    for h in sorted(hits, key=lambda h: h.date.day):
        for g in groups:
            if _day_gap(statistics.median_low(x.date.day for x in g), h.date.day) <= DAY_TOLERANCE:
                g.append(h)
                break
        else:
            groups.append([h])
    return groups


def _on_day(month: str, day: int) -> dt.date:
    first = month_start(month)
    return first.replace(day=min(day, calendar.monthrange(first.year, first.month)[1]))


def _card_balance(conn: Connection, account: str, as_of: dt.date, fx: Fx) -> int:
    rows = conn.execute(
        text(
            "SELECT commodity, SUM(amount) AS amt FROM v_postings WHERE account = :a AND date <= :d"
            " GROUP BY commodity"
        ),
        {"a": account, "d": as_of.isoformat()},
    ).all()
    return sum(fx.to_base(r.amt, r.commodity, as_of.isoformat()) for r in rows)


@dataclass
class Pattern:
    """One recurring series found in the ledger: a rent, a subscription, a paycheck."""

    key: str  # merchant|account|in or out: what a dismissal remembers
    name: str
    account: str
    kind: str  # bill | income | card_payment | transfer
    cadence: str  # monthly | annual
    day: int  # day of the month it usually lands on (annual: of its month)
    amount: int  # cents, ledger sign: what the next one is expected to be
    estimated: bool
    category: str | None
    last_date: dt.date
    last_amount: int
    before_amount: int | None  # the usual amount before the last charge
    count: int  # charges seen
    next_date: dt.date | None
    dismissed: bool = False
    # The same amounts as the statement has them (a ¥21 subscription stays ¥21 whatever the rate).
    commodity: str = "USD"
    native_amount: int = 0
    native_last: int = 0
    native_before: int | None = None
    tentative: bool = False  # only two charges so far, identical and a month apart


def _native(last: list[_Hit]) -> dict[str, Any]:
    """The series' amounts in the statement's own currency (when it has one currency)."""
    ccy = last[-1].commodity
    if any(h.commodity != ccy for h in last):
        return {}
    earlier = [h.native for h in last[-4:-1]]
    return {
        "commodity": ccy,
        "native_amount": int(statistics.median(h.native for h in last[-3:])),
        "native_last": last[-1].native,
        "native_before": int(statistics.median(earlier)) if earlier else None,
    }


def _young_series(cluster: list[_Hit], key: str, as_of: dt.date) -> Pattern | None:
    """A subscription that has only just begun: exactly two charges of the same amount about a
    month apart, the later one recent. Listed (so it is seen early), never forecast."""
    if len(cluster) != 2:
        return None
    a, b = sorted(cluster, key=lambda h: h.date)
    if (
        not 26 <= (b.date - a.date).days <= 35
        or a.native != b.native
        or a.commodity != b.commodity
        or b.amount >= 0
        or b.kind != "normal"
        or (as_of - b.date).days > 45
    ):
        return None
    upcoming = _on_day(add_months(b.date.strftime("%Y-%m"), 1), b.date.day)
    return Pattern(
        key=key,
        name=b.name,
        account=b.account,
        kind="bill",
        cadence="monthly",
        day=b.date.day,
        amount=b.amount,
        estimated=False,
        category=b.category,
        last_date=b.date,
        last_amount=b.amount,
        before_amount=a.amount,
        count=2,
        next_date=upcoming if (as_of - upcoming).days <= 10 else None,
        commodity=b.commodity,
        native_amount=b.native,
        native_last=b.native,
        native_before=a.native,
        tentative=True,
    )


def dismissed_patterns(conn: Connection) -> set[str]:
    return {r.key for r in conn.execute(text("SELECT key FROM recurring_dismissed"))}


def recurring_events(
    conn: Connection,
    as_of: dt.date,
    fx: Fx,
    horizon: int = HORIZON_DAYS,
    *,
    patterns: list[Pattern] | None = None,
) -> tuple[list[Event], set[tuple[str, dt.date, int]]]:
    """Expected events from (as_of - a few days, as_of + horizon], and the identities of past hits
    that belong to a monthly pattern (date, account, amount) so callers can separate fixed bills
    from discretionary spending.

    A series the user dismissed ("this is not a bill") gives no events and no fixed spending.
    `patterns`, when given, collects every series found, dismissed ones included."""
    dismissed = dismissed_patterns(conn)
    month = as_of.strftime("%Y-%m")
    window_start = month_start(add_months(month, -HISTORY_MONTHS))
    hits = _unrefunded(
        _hits(conn, month_start(add_months(month, -25)), as_of, fx, wallets=patterns is not None)
    )
    by_key: defaultdict[tuple[str, str, bool], list[_Hit]] = defaultdict(list)
    for h in hits:
        if h.kind == "transfer" and h.amount > 0:
            continue  # a transfer is predicted once, from the side the money leaves
        # Same merchant into / out of the same account. Sign matters: a refund is not a bill.
        by_key[(merchant_key(h.name), h.account, h.amount > 0)].append(h)

    recent_months = {add_months(month, -i) for i in range(1, HISTORY_MONTHS + 1)}
    events: list[Event] = []
    fixed: set[tuple[str, dt.date, int]] = set()
    end = as_of + dt.timedelta(days=horizon)
    for (merchant, account, inflow), group in by_key.items():
        key = f"{merchant}|{account}|{'in' if inflow else 'out'}"
        # Dismissed by the user, or paid from a wallet: listed as a series, never forecast.
        ignore = key in dismissed or group[0].wallet
        if ignore and patterns is None:
            continue
        monthly_found = False
        in_window = [h for h in group if h.date >= window_start]
        active = {h.date.strftime("%Y-%m") for h in in_window}
        if active and len(in_window) / len(active) > 2.5:
            continue  # a habit (groceries, fuel), not a bill: several times a month on any day
        for cluster in _clusters(in_window):
            months = {h.date.strftime("%Y-%m") for h in cluster}
            if len(months & recent_months) < min(4, max(3, len(active & recent_months) - 1)):
                if patterns is not None and (t := _young_series(cluster, key, as_of)) is not None:
                    t.dismissed = key in dismissed
                    patterns.append(t)
                    monthly_found = True
                continue
            if len(cluster) < 0.4 * len(in_window):
                continue  # most of this merchant's charges fall on other days
            days = [h.date.day for h in cluster]
            centre = statistics.median(days)
            if statistics.median(_day_gap(round(centre), d) for d in days) > 1.5:
                continue  # scattered around the window, not booked on a fixed day
            last = sorted(cluster, key=lambda h: h.date)
            sample = last[0]
            card_payment = sample.kind == "transfer" and sample.other_type == "Liabilities"
            amounts = [h.amount for h in last]
            med = statistics.median(amounts)
            if not card_payment and sum(
                abs(a - med) <= abs(med) * 0.5 for a in amounts
            ) < 0.75 * len(amounts):
                continue  # same day, wildly different amounts: not one bill
            monthly_found = True
            for h in cluster:
                # fixed spending, not investing
                if not ignore and (h.category or "").startswith("Expenses:"):
                    fixed.add((h.account, h.date, h.amount))
            day = int(statistics.median_low(h.date.day for h in cluster))
            recent = [h.amount for h in last[-3:]]
            amount = int(statistics.median(recent))
            estimated = len(set(recent)) > 1
            if card_payment and sample.other_account:
                balance = _card_balance(conn, sample.other_account, as_of, fx)
                amount, estimated = (balance if balance < 0 else 0), True
            moves_own_money = (sample.category or "").startswith(("Assets:", "Liabilities:"))
            if sample.kind == "transfer" or moves_own_money:
                # e.g. a monthly brokerage contribution categorised to its asset account
                kind = "card_payment" if card_payment else "transfer"
            else:
                kind = "income" if amount > 0 else "bill"
            seen_this_month = month in months
            if patterns is not None:
                # The month after the last charge; more than ten days late means it stopped.
                upcoming = _on_day(add_months(last[-1].date.strftime("%Y-%m"), 1), day)
                if upcoming <= as_of and (as_of - upcoming).days > 10:
                    upcoming = None
                earlier = amounts[-4:-1]
                patterns.append(
                    Pattern(
                        key=key,
                        name=sample.name,
                        account=account,
                        kind=kind,
                        cadence="monthly",
                        day=day,
                        amount=amount,
                        estimated=estimated,
                        category=last[-1].category,
                        last_date=last[-1].date,
                        last_amount=last[-1].amount,
                        before_amount=int(statistics.median(earlier)) if earlier else None,
                        count=len(cluster),
                        next_date=upcoming,
                        dismissed=key in dismissed,
                        **_native(last),
                    )
                )
            if ignore:
                continue
            for k in range(0, 3):
                m = add_months(month, k)
                date = _on_day(m, day)
                if k == 0 and seen_this_month:
                    continue
                overdue = k == 0 and date <= as_of
                if overdue and (as_of - date).days > 10:
                    continue  # long gone: probably stopped
                if date > end or (amount == 0 and kind == "card_payment"):
                    continue
                events.append(
                    Event(
                        date=date,
                        name=sample.name,
                        amount=amount,
                        kind=kind,
                        account=account,
                        cadence="monthly",
                        category=sample.category,
                        estimated=estimated,
                        overdue=overdue,
                        counterparty=sample.other_account,
                    )
                )
        if monthly_found or len(group) < 2:
            continue
        # Annual: the last two hits ~a year apart with a similar amount.
        g = sorted(group, key=lambda h: h.date)
        a, b = g[-2], g[-1]
        gap = (b.date - a.date).days
        if 330 <= gap <= 400 and abs(a.amount - b.amount) <= abs(b.amount) * 0.25:
            due = (
                b.date.replace(year=b.date.year + 1)
                if not (b.date.month == 2 and b.date.day == 29)
                else dt.date(b.date.year + 1, 2, 28)
            )
            if patterns is not None:
                patterns.append(
                    Pattern(
                        key=key,
                        name=b.name,
                        account=b.account,
                        kind="income" if b.amount > 0 else "bill",
                        cadence="annual",
                        day=b.date.day,
                        amount=b.amount,
                        estimated=False,
                        category=b.category,
                        last_date=b.date,
                        last_amount=b.amount,
                        before_amount=a.amount,
                        count=len(g),
                        next_date=due if due > as_of - dt.timedelta(days=10) else None,
                        dismissed=key in dismissed,
                        commodity=b.commodity,
                        native_amount=b.native,
                        native_last=b.native,
                        native_before=a.native if a.commodity == b.commodity else None,
                    )
                )
            if not ignore and as_of < due <= as_of + dt.timedelta(days=max(horizon, 45)):
                events.append(
                    Event(
                        date=due,
                        name=b.name,
                        amount=b.amount,
                        kind="income" if b.amount > 0 else "bill",
                        account=b.account,
                        cadence="annual",
                        category=b.category,
                    )
                )
    events.sort(key=lambda e: (e.date, e.amount))
    return events, fixed


# ---------------------------------------------------------------- cash projection
def _cash_accounts(conn: Connection) -> list[str]:
    return [
        r.name
        for r in conn.execute(
            text(
                "SELECT name FROM accounts WHERE type = 'Assets' AND flow_only = 0"
                " AND COALESCE(asset_class, '') = 'cash'"
            )
        )
    ]


def _balance(conn: Connection, accounts: list[str], as_of: dt.date, fx: Fx) -> int:
    if not accounts:
        return 0
    rows = conn.execute(
        text(
            "SELECT commodity, SUM(amount) AS amt FROM v_postings"
            " WHERE account IN (SELECT value FROM json_each(:a)) AND date <= :d GROUP BY commodity"
        ),
        {"a": json.dumps(accounts), "d": as_of.isoformat()},
    ).all()
    return sum(fx.to_base(r.amt, r.commodity, as_of.isoformat()) for r in rows)


@dataclass
class CashFlowDay:
    date: dt.date
    balance: int  # cash accounts at the end of the day
    inflow: int  # income booked that day, positive
    outflow: int  # spending booked that day (any account), positive


def cash_history(
    conn: Connection, start: dt.date, end: dt.date, fx: Fx | None = None
) -> list[CashFlowDay]:
    """Day by day from `start` to `end`: the cash accounts' balance, and the money that came in
    and went out. Revaluations and opening balances are not money moving, so they are left out of
    the flows (they still count in the balance, which is what the account statement shows)."""
    fx = fx or Fx(conn)
    cash = _cash_accounts(conn)
    bal = _balance(conn, cash, start - dt.timedelta(days=1), fx)
    moves: defaultdict[dt.date, int] = defaultdict(int)
    inflow: defaultdict[dt.date, int] = defaultdict(int)
    params = {"a": json.dumps(cash), "s": start.isoformat(), "e": end.isoformat()}
    for r in conn.execute(
        text(
            "SELECT date, commodity, SUM(amount) AS amt FROM v_postings"
            " WHERE account IN (SELECT value FROM json_each(:a)) AND date BETWEEN :s AND :e"
            " GROUP BY date, commodity"
        ),
        params,
    ):
        moves[dt.date.fromisoformat(r.date)] += fx.to_base(r.amt, r.commodity, r.date)
    for r in conn.execute(
        text(
            "SELECT date, commodity, SUM(amount) AS amt FROM v_postings"
            " WHERE account_type = 'Income' AND kind NOT IN ('adjustment', 'opening')"
            " AND date BETWEEN :s AND :e GROUP BY date, commodity"
        ),
        params,
    ):
        inflow[dt.date.fromisoformat(r.date)] += -fx.to_base(r.amt, r.commodity, r.date)
    spend = _daily_spend(conn, start, end, fx)
    out: list[CashFlowDay] = []
    d = start
    while d <= end:
        bal += moves[d]
        out.append(CashFlowDay(d, bal, inflow[d], spend.get(d, 0)))
        d += dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------- the pulse
def pulse(conn: Connection, as_of: dt.date | None = None, fx: Fx | None = None) -> Pulse | None:
    if as_of is None:
        if latest_date(conn) is None:
            return None
        # Import freshness is not today's date: the live month and the calendar must advance
        # even on a day (or at the start of a month) with no new transactions.
        as_of = dt.date.today()
    prefs = load_prefs(conn)
    fx = fx or Fx(conn)
    month = as_of.strftime("%Y-%m")
    dim = calendar.monthrange(as_of.year, as_of.month)[1]
    day = as_of.day

    hist_start = month_start(add_months(month, -HISTORY_MONTHS))
    daily = _daily_spend(conn, hist_start, as_of, fx)
    past = [add_months(month, -i) for i in range(HISTORY_MONTHS, 0, -1)]
    totals = {m: sum(v for d, v in daily.items() if d.strftime("%Y-%m") == m) for m in past}
    past = [m for m in past if totals[m] > 0]  # months before the ledger starts don't count
    curves = {m: _cumulative(daily, m, 31) for m in past}
    actual = _cumulative(daily, month, day)
    spent = actual[-1] if actual else 0

    curve: list[CurvePoint] = []
    for d in range(1, dim + 1):
        if past:
            low, typ, high = _band([curves[m][d - 1] for m in past])
        else:
            low = typ = high = 0
        curve.append(CurvePoint(d, actual[d - 1] if d <= day else None, typ, low, high))
    typical_month = int(statistics.median(totals[m] for m in past)) if past else 0
    typical_to_date = curve[day - 1].typical if curve else 0

    events, fixed = recurring_events(conn, as_of, fx)
    month_end_date = month_end(month)
    remaining_bills = -sum(
        e.amount
        for e in events
        if e.kind == "bill" and (as_of < e.date <= month_end_date or e.overdue)
    )
    # Discretionary pace: what a usual month spends outside its fixed bills, per day.
    fixed_by_month: defaultdict[str, int] = defaultdict(int)
    for _, d, amt in fixed:
        if amt < 0:
            fixed_by_month[d.strftime("%Y-%m")] += -amt
    disc = [
        max(0, totals[m] - fixed_by_month[m]) / calendar.monthrange(*map(int, m.split("-")))[1]
        for m in past
    ]
    per_day = int(statistics.median(disc)) if disc else 0
    days_left = dim - day
    forecast = spent + remaining_bills + per_day * days_left

    entered_in = load_assumptions().base_currency  # Settings asks for amounts in the main currency
    if prefs.monthly_budget is not None:
        budget = fx.to_base(int(Decimal(prefs.monthly_budget) * 100), entered_in, as_of.isoformat())
        source = "set"
    else:
        budget, source = typical_month, "typical"
    safe = (
        max(0, budget - spent - remaining_bills) // days_left if days_left > 0 and budget else None
    )

    # Cash accounts: today's balance, then every expected event that touches them, plus the
    # usual day-to-day outflow from those accounts that is not a recurring event.
    cash = _cash_accounts(conn)
    cash_now = _balance(conn, cash, as_of, fx)
    cash_set = set(cash)
    hits = _hits(conn, hist_start, as_of, fx)
    loose = [
        h
        for h in hits
        if h.account in cash_set
        and h.amount < 0
        and h.kind == "normal"
        and (h.account, h.date, h.amount) not in fixed
        and h.date < month_start(month)
    ]
    loose_per_day = (
        -sum(h.amount for h in loose) // max(1, (month_start(month) - hist_start).days)
        if loose
        else 0
    )
    # Conservative: a bill that is late is assumed to still come out tomorrow; a late paycheck is
    # not counted until it shows up.
    late_bills = sum(
        e.amount for e in events if e.account in cash_set and e.overdue and e.amount < 0
    )
    series, bal = [CashPoint(as_of, cash_now)], cash_now + late_bills
    for i in range(1, HORIZON_DAYS + 1):
        d = as_of + dt.timedelta(days=i)
        bal += sum(
            e.amount for e in events if e.account in cash_set and e.date == d and not e.overdue
        )
        bal -= loose_per_day
        series.append(CashPoint(d, bal))
    low_point = min(series[1:], key=lambda c: c.balance)
    low, low_date = low_point.balance, low_point.date
    floor = fx.to_base(int(Decimal(prefs.low_balance) * 100), entered_in, as_of.isoformat())

    p = Pulse(
        as_of=as_of,
        month=month,
        days_in_month=dim,
        spent=spent,
        typical_to_date=typical_to_date,
        typical_month=typical_month,
        budget=budget,
        budget_source=source,
        forecast=forecast,
        remaining_bills=remaining_bills,
        safe_per_day=safe,
        curve=curve,
        upcoming=[e for e in events if e.date > as_of or e.overdue],
        cash_now=cash_now,
        cash_low=low,
        cash_low_date=low_date,
        low_balance=floor,
        cash_series=series,
        history_months=len(past),
    )
    p.alerts = _alerts(p, bool(cash), fx.base)
    return p


def _alerts(p: Pulse, has_cash: bool, currency: str = "USD") -> list[Alert]:
    def money(cents: int) -> str:
        symbol = "¥" if currency == "CNY" else "$"
        digits = 0 if abs(cents) >= 10_000 else 2
        return f"{symbol}{abs(cents) / 100:,.{digits}f}"

    out: list[Alert] = []
    m = p.as_of.strftime("%Y-%m")
    if p.history_months >= 2 and p.budget:
        if p.spent > p.budget:
            out.append(
                Alert(
                    f"over-budget-{m}",
                    "warning",
                    tr("本月已超出预算"),
                    tr(
                        "已经花了 {spent}，预算 {budget}，超出 {over}。",
                        spent=money(p.spent),
                        budget=money(p.budget),
                        over=money(p.spent - p.budget),
                    ),
                    "/details?tab=spending",
                )
            )
        elif p.forecast > p.budget * 1.1:
            out.append(
                Alert(
                    f"pace-{m}",
                    "warning",
                    tr("照这个速度，月底会超支"),
                    tr(
                        "预计本月花 {forecast}，比{basis} {budget} 多 {over}。之后每天控制在 {safe} 以内就能守住。",
                        forecast=money(p.forecast),
                        basis=tr("预算") if p.budget_source == "set" else tr("平时"),
                        budget=money(p.budget),
                        over=money(p.forecast - p.budget),
                        safe=money(p.safe_per_day or 0),
                    ),
                    "/details?tab=spending",
                )
            )
    for e in p.upcoming:
        if e.overdue and e.kind == "income" and (p.as_of - e.date).days >= 3:
            out.append(
                Alert(
                    f"missing-income-{e.name}-{e.date}",
                    "warning",
                    tr("{name} 还没到账", name=e.name),
                    tr(
                        "通常在每月 {day} 日左右收到约 {amount}，这个月到 {as_of} 还没看到。如果已经到账，导入最新账单即可。",
                        day=e.date.day,
                        amount=money(e.amount),
                        as_of=p.as_of.isoformat(),
                    ),
                    "/details?tab=calendar",
                )
            )
    if has_cash and p.cash_low_date and p.cash_low < p.low_balance:
        out.append(
            Alert(
                f"low-cash-{p.cash_low_date}",
                "warning",
                tr("现金可能不够"),
                tr(
                    "按已知的账单和工资，{date} 前后现金账户合计约 {low}，低于你设的 {floor}。可以提前转一笔钱进来。",
                    date=p.cash_low_date.isoformat(),
                    low=("−" if p.cash_low < 0 else "") + money(p.cash_low),
                    floor=money(p.low_balance),
                ),
                "/details?tab=calendar",
            )
        )
    week = p.as_of + dt.timedelta(days=7)
    soon = [e for e in p.upcoming if e.amount < 0 and p.as_of < e.date <= week]
    if soon:
        total = -sum(e.amount for e in soon)
        names = tr("、").join(e.name for e in soon[:3])
        out.append(
            Alert(
                f"week-{p.as_of}",
                "info",
                tr("未来 7 天要付 {n} 笔，共 {total}", n=len(soon), total=money(total)),
                tr("包括 {names}{more}。", names=names, more=tr(" 等") if len(soon) > 3 else ""),
                "/details?tab=calendar",
            )
        )
    for e in p.upcoming:
        if e.cadence == "annual" and e.amount < 0:
            out.append(
                Alert(
                    f"annual-{e.name}-{e.date}",
                    "notice",
                    tr("{name} 年费快到了", name=e.name),
                    tr(
                        "去年 {last} 扣了 {amount}，预计 {date} 再扣一次。不想续可以提前取消。",
                        last=e.date.replace(year=e.date.year - 1).isoformat(),
                        amount=money(e.amount),
                        date=e.date.isoformat(),
                    ),
                    "/details?tab=calendar",
                )
            )
    order = {"warning": 0, "notice": 1, "info": 2}
    return sorted(out, key=lambda a: order[a.severity])
