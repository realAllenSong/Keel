"""Positions, gains and trades of brokerage accounts (the tables of pf/ledger/portfolio.py).

- `positions`: what each account holds on its newest broker report (on or before a day), with
  the broker's own cost basis, the unrealised gain and the move since the report before it.
- `realized`: gains on closed lots, first in first out, from the trade events. Matching is done
  on cash (what a lot cost or brought in), so stocks, fractional shares and options (whose prices
  are per share and whose contracts are 100) need no special arithmetic. A sale of something
  bought before the broker's history starts has no known cost: it is listed as `unmatched`
  instead of being given a made-up gain.
- `trades`: the events themselves, for reading and for the compliance report.
- `statement`: accounts, positions on a day and trades in a period, in one object: what a
  brokerage-account certification asks for.

Money is in each position's own currency; totals are in the report currency (latest rate)."""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from pf.importers.brokers import DIVIDENDS
from pf.reports.common import Fx

ZERO = Decimal(0)
CLOSING = {"EXPIRATION", "ASSIGNMENT", "EXERCISE"}
OPENING_SIGN = {"BUY": 1, "REINVEST": 1, "SELL": -1}
_OCC = re.compile(
    r"^(?P<root>[A-Z.]+)\s*(?P<y>\d\d)(?P<m>\d\d)(?P<d>\d\d)(?P<cp>[CP])(?P<k>\d{8})$"
)


def label(symbol: str) -> str:
    """'NOK 260605P00014500' -> 'NOK 2026-06-05 14.5 PUT'; anything else unchanged."""
    m = _OCC.match(symbol.replace(" ", ""))
    if not m:
        return symbol
    strike = (Decimal(m["k"]) / 1000).normalize()
    side = "PUT" if m["cp"] == "P" else "CALL"
    return f"{m['root']} 20{m['y']}-{m['m']}-{m['d']} {strike:f} {side}"


def _rate(fx: Fx, currency: str, day: str) -> Decimal:
    """The rate into the report currency, or zero with the currency noted as missing: an
    amount that cannot be converted is left out and said, never guessed."""
    rate = fx.rate(currency, day)
    if rate is None:
        fx.missing.add(currency)
        return ZERO
    return rate


def _q(x: Decimal, places: str = "0.01") -> str:
    return str(x.quantize(Decimal(places)))


def _num(x: Decimal) -> str:
    """A quantity as people write it: 6.742, 100, 0.0784."""
    s = f"{x.normalize():f}"
    return s if s != "-0" else "0"


# ---------------------------------------------------------------- positions
@dataclass
class Position:
    account: str
    institution: str | None
    symbol: str
    name: str  # the symbol in words for options, else the symbol
    kind: str | None
    quantity: str
    price: str | None
    value: str
    cost: str | None  # what the position cost (the broker's figure)
    gain: str | None  # value - cost
    gain_pct: float | None
    day_change: str | None  # value move since the report before this one
    currency: str
    cash_equivalent: bool
    as_of: str


@dataclass
class AccountHoldings:
    account: str
    institution: str | None
    as_of: str
    previous: str | None  # the report `day_change` compares with
    value: str  # in the report currency
    positions: list[Position] = field(default_factory=list)


@dataclass
class Holdings:
    currency: str
    as_of: str | None  # the newest report among the accounts
    value: str
    invested: str  # value of positions that are not cash
    cash: str  # money-market and cash positions
    cost: str | None  # of the positions whose cost is known
    gain: str | None
    gain_pct: float | None
    day_change: str | None
    accounts: list[AccountHoldings] = field(default_factory=list)
    missing_fx: list[str] = field(default_factory=list)
    quoted_at: str | None = (
        None  # prices are delayed market quotes from this moment, not the report's
    )


def _snapshots(conn: Connection, on: dt.date | None) -> list:
    """Each account's newest snapshot on or before `on`, and the one before it."""
    return list(
        conn.execute(
            text(
                """
                SELECT s.account_id, a.name AS account, a.institution, a.asset_class,
                       MAX(s.as_of) AS as_of,
                       (SELECT MAX(p.as_of) FROM holding_snapshots p
                         WHERE p.account_id = s.account_id AND p.as_of < MAX(s.as_of)) AS previous
                FROM holding_snapshots s JOIN accounts a ON a.id = s.account_id
                WHERE (:on IS NULL OR s.as_of <= :on)
                GROUP BY s.account_id ORDER BY a.name
                """
            ),
            {"on": on.isoformat() if on else None},
        )
    )


def _rows(conn: Connection, account_id: int, as_of: str | None) -> dict[str, Any]:
    if as_of is None:
        return {}
    return {
        r.symbol: r
        for r in conn.execute(
            text("SELECT * FROM holdings WHERE account_id = :a AND as_of = :d ORDER BY symbol"),
            {"a": account_id, "d": as_of},
        )
    }


def positions(
    conn: Connection,
    on: dt.date | None = None,
    currency: str = "USD",
    quotes: dict[str, tuple[float, str]] | None = None,
) -> Holdings:
    """`quotes` ({symbol: (price, time)}, delayed market quotes) replace the broker report's
    prices for US-dollar shares and funds: quantities and costs stay the broker's, values and
    gains follow the market since the report. The move shown is then since that report."""
    fx = Fx(conn, currency)
    quoted_at: str | None = None
    total = invested = cash = cost_known = value_with_cost = day = ZERO
    any_cost = any_day = False
    accounts: list[AccountHoldings] = []
    newest: str | None = None
    for s in _snapshots(conn, on):
        now, before = _rows(conn, s.account_id, s.as_of), _rows(conn, s.account_id, s.previous)
        acc = AccountHoldings(s.account, s.institution, s.as_of, s.previous, "0")
        acc_value = ZERO
        for sym, r in now.items():
            qty = Decimal(r.quantity)
            price = Decimal(r.price) if r.price is not None else None
            reported = price
            live = (quotes or {}).get(sym)
            # Stock quotes are for stocks: on an exchange account "BTC" is a coin, not a ticker.
            listed = s.asset_class != "crypto" and (r.kind or "").lower() not in NOT_LISTED
            if live and listed and r.currency == "USD" and not r.cash_equivalent:
                price = Decimal(str(round(live[0], 4)))
                quoted_at = max(quoted_at, live[1]) if quoted_at else live[1]
            value = qty * price if price is not None else ZERO
            cost = Decimal(r.cost_basis) if r.cost_basis is not None else None
            is_cash = bool(r.cash_equivalent)
            gain = value - cost if cost is not None and not is_cash else None
            change = None
            prev = before.get(sym)
            if live and price is not None and reported is not None and price != reported:
                change = (price - reported) * qty  # since the broker's report
            elif prev is not None and price is not None and prev.price is not None and not is_cash:
                # the price move on what is held now (a buy or sale is not a gain)
                change = (price - Decimal(prev.price)) * qty
            rate = _rate(fx, r.currency, s.as_of)
            acc_value += value * rate
            if is_cash:
                cash += value * rate
            else:
                invested += value * rate
                if cost is not None:
                    any_cost = True
                    cost_known += cost * rate
                    value_with_cost += value * rate
                if change is not None:
                    any_day = True
                    day += change * rate
            acc.positions.append(
                Position(
                    account=s.account,
                    institution=s.institution,
                    symbol=sym,
                    name=label(sym),
                    kind=r.kind,
                    quantity=_num(qty),
                    price=_num(price) if price is not None else None,
                    value=_q(value),
                    cost=_q(cost) if cost is not None and not is_cash else None,
                    gain=_q(gain) if gain is not None else None,
                    gain_pct=float(gain / cost) if gain is not None and cost else None,
                    day_change=_q(change) if change is not None else None,
                    currency=r.currency,
                    cash_equivalent=is_cash,
                    as_of=s.as_of,
                )
            )
        acc.positions.sort(key=lambda p: (p.cash_equivalent, -Decimal(p.value)))
        acc.value = _q(acc_value)
        total += acc_value
        accounts.append(acc)
        newest = max(newest, s.as_of) if newest else s.as_of
    gain = value_with_cost - cost_known if any_cost else None
    return Holdings(
        currency=currency,
        as_of=newest,
        value=_q(total),
        invested=_q(invested),
        cash=_q(cash),
        cost=_q(cost_known) if any_cost else None,
        gain=_q(gain) if gain is not None else None,
        gain_pct=float(gain / cost_known) if gain is not None and cost_known else None,
        day_change=_q(day) if any_day else None,
        accounts=accounts,
        missing_fx=sorted(fx.missing),
        quoted_at=quoted_at,
    )


def live_positions(conn: Connection, currency: str = "USD", fetch=None) -> Holdings:  # type: ignore[no-untyped-def]
    """Positions valued at delayed market quotes; the broker's own prices when quotes fail."""
    from pf.market.providers.prices import latest_prices

    held = positions(conn, None, currency)
    symbols = [
        p.symbol
        for a in held.accounts
        for p in a.positions
        if not p.cash_equivalent
        and (p.kind or "").lower() not in NOT_LISTED
        and p.currency == "USD"
    ]
    if not symbols:
        return held
    try:
        quotes = (fetch or latest_prices)(symbols)
    except Exception:
        return held
    return positions(conn, None, currency, quotes) if quotes else held


# ---------------------------------------------------------------- trades
@dataclass
class Trade:
    id: int
    date: str
    account: str
    institution: str | None
    action: str
    symbol: str
    name: str
    underlying: str | None
    kind: str | None
    quantity: str
    price: str | None
    amount: str
    currency: str
    description: str | None
    cash_equivalent: bool


def _cash_symbols(conn: Connection) -> set[tuple[int, str]]:
    """(account, symbol) the broker ever called a cash equivalent: its sweep, not a trade."""
    return {
        (r.account_id, r.symbol)
        for r in conn.execute(
            text("SELECT DISTINCT account_id, symbol FROM holdings WHERE cash_equivalent = 1")
        )
    }


def _is_cash(row, cash: set[tuple[int, str]]) -> bool:  # type: ignore[no-untyped-def]
    if (row.account_id, row.symbol) in cash:
        return True
    # A money-market fund never seen in a position report: an open-ended fund at exactly 1.00.
    return row.kind == "oef" and row.price is not None and Decimal(row.price) == 1


def trades(
    conn: Connection,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    account: str | None = None,
    symbol: str | None = None,
    include_cash: bool = False,
) -> list[Trade]:
    """Newest first. Cash-sweep movements (money market in and out) are left out unless asked."""
    rows = conn.execute(
        text(
            """
            SELECT t.*, a.name AS account, a.institution FROM trades t
            JOIN accounts a ON a.id = t.account_id
            WHERE (:s IS NULL OR t.date >= :s) AND (:e IS NULL OR t.date <= :e)
              AND (:acc IS NULL OR a.name = :acc)
              AND (:sym IS NULL OR t.symbol = :sym OR t.underlying = :sym)
            ORDER BY t.date DESC, t.id DESC
            """
        ),
        {
            "s": start.isoformat() if start else None,
            "e": end.isoformat() if end else None,
            "acc": account,
            "sym": symbol.upper() if symbol else None,
        },
    ).all()
    cash = _cash_symbols(conn)
    out = []
    for r in rows:
        is_cash = _is_cash(r, cash)
        if is_cash and not include_cash:
            continue
        out.append(
            Trade(
                id=r.id,
                date=r.date,
                account=r.account,
                institution=r.institution,
                action=r.action,
                symbol=r.symbol,
                name=label(r.symbol),
                underlying=r.underlying,
                kind=r.kind,
                quantity=_num(Decimal(r.quantity)),
                price=_num(Decimal(r.price)) if r.price is not None else None,
                amount=_q(Decimal(r.amount)),
                currency=r.currency,
                description=r.description,
                cash_equivalent=is_cash,
            )
        )
    return out


# ---------------------------------------------------------------- realised gains
@dataclass
class Closed:
    date: str  # when the lot was closed
    opened: str
    account: str
    symbol: str
    name: str
    quantity: str
    cost: str  # what the closed part cost (for a short: what closing it cost)
    proceeds: str
    gain: str
    long_term: bool  # held more than a year
    currency: str
    kind: str | None = None
    underlying: str | None = None
    opened_id: int | None = None  # the trade that opened the lot
    wash: str | None = None  # the part of a loss a wash sale would disallow (estimate)


@dataclass
class Unmatched:
    """A sale with no purchase in the broker's history: the cost is not known."""

    date: str
    account: str
    symbol: str
    name: str
    quantity: str
    proceeds: str
    currency: str


@dataclass
class Sale:
    """Every lot one closing trade closed, as one line (a sale of shares bought in twenty small
    purchases is still one sale)."""

    date: str
    account: str
    symbol: str
    name: str
    quantity: str
    cost: str
    proceeds: str
    gain: str
    long_term: bool
    currency: str
    legs: int = 1  # an option position of several contracts closed together counts as one
    wash: str | None = None


@dataclass
class TaxYear:
    """A year's realised gains the way a tax form splits them. An estimate: first in first out,
    and wash sales by a simple test; the broker's 1099-B is the figure that counts."""

    short_term: str
    long_term: str
    wash_disallowed: str  # losses a wash sale would not let you deduct now


@dataclass
class Realized:
    currency: str
    by_year: dict[str, str]
    tax: dict[str, TaxYear] = field(default_factory=dict)
    sales: list[Sale] = field(default_factory=list)
    closed: list[Closed] = field(default_factory=list)
    unmatched: list[Unmatched] = field(default_factory=list)
    missing_fx: list[str] = field(default_factory=list)


def _sign(x: Decimal) -> int:
    return (x > 0) - (x < 0)


def realized(conn: Connection, currency: str = "USD") -> Realized:
    rows = conn.execute(
        text(
            "SELECT t.*, a.name AS account FROM trades t JOIN accounts a ON a.id = t.account_id"
            " ORDER BY t.account_id, t.symbol, t.date, t.id"
        )
    ).all()
    cash = _cash_symbols(conn)
    groups: dict[tuple[int, str], list] = defaultdict(list)
    for r in rows:
        if not _is_cash(r, cash):
            groups[(r.account_id, r.symbol)].append(r)

    closed: list[Closed] = []
    unmatched: list[Unmatched] = []
    for events in groups.values():
        # [quantity (signed), cash (signed, + = received), opened, id of the opening trade]
        lots: deque[list] = deque()
        for r in events:
            held = sum((lot[0] for lot in lots), ZERO)
            qty, amount = Decimal(r.quantity), Decimal(r.amount)
            if r.action in ("SPLIT", "STOCK_DIVIDEND"):
                if held and held + qty != 0:
                    factor = (held + qty) / held
                    for lot in lots:
                        lot[0] *= factor
                continue
            if r.action in CLOSING:
                if not held:
                    continue  # the option was opened before the history starts
                delta, amount = -_sign(held) * abs(qty), ZERO
            elif r.action in OPENING_SIGN:
                delta = OPENING_SIGN[r.action] * abs(qty)
            else:
                continue
            if not delta:
                continue
            left = delta
            while left and lots and _sign(lots[0][0]) != _sign(left):
                lot = lots[0]
                part = min(abs(left), abs(lot[0]))
                lot_cash = lot[1] * part / abs(lot[0])
                this_cash = amount * part / abs(delta)
                # a long lot: paid then, received now; a short lot: received then, paid now
                cost, proceeds = (-lot_cash, this_cash) if lot[0] > 0 else (-this_cash, lot_cash)
                days = (dt.date.fromisoformat(r.date) - dt.date.fromisoformat(lot[2])).days
                closed.append(
                    Closed(
                        date=r.date,
                        opened=lot[2],
                        account=r.account,
                        symbol=r.symbol,
                        name=label(r.symbol),
                        quantity=_num(part),
                        cost=_q(cost),
                        proceeds=_q(proceeds),
                        gain=_q(proceeds - cost),
                        long_term=days > 365,
                        currency=r.currency,
                        kind=r.kind,
                        underlying=r.underlying,
                        opened_id=lot[3],
                    )
                )
                lot[0] -= _sign(lot[0]) * part
                lot[1] -= lot_cash
                left -= _sign(left) * part
                if not lot[0]:
                    lots.popleft()
            if not left:
                continue
            rest_cash = amount * abs(left) / abs(delta)
            if left < 0 and r.kind != "option":
                # Selling shares the history never saw bought: no cost to compare with.
                unmatched.append(
                    Unmatched(
                        date=r.date,
                        account=r.account,
                        symbol=r.symbol,
                        name=label(r.symbol),
                        quantity=_num(abs(left)),
                        proceeds=_q(rest_cash),
                        currency=r.currency,
                    )
                )
                continue
            lots.append([left, rest_cash, r.date, r.id])  # a new lot (a sold option opens a short)

    fx = Fx(conn, currency)
    by_year: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for c in closed:
        by_year[c.date[:4]] += Decimal(c.gain) * (_rate(fx, c.currency, c.date))
    _wash_sales(closed, rows, cash)
    tax_sums: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO, ZERO])
    for c in closed:
        rate = _rate(fx, c.currency, c.date)
        t = tax_sums[c.date[:4]]
        t[1 if c.long_term else 0] += Decimal(c.gain) * rate
        t[2] += Decimal(c.wash or 0) * rate

    # An option position of several contracts on one underlying and expiry (a spread, a strangle)
    # is one decision: its legs are shown and summed as one line, dated when the last one closed.
    spread: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for c in closed:
        if c.kind == "option" and (m := _OCC.match(c.symbol.replace(" ", ""))):
            spread[(c.account, c.underlying or m["root"], f"20{m['y']}-{m['m']}-{m['d']}")].add(
                c.symbol
            )
    grouped: dict[tuple, list] = {}
    for c in closed:
        key: tuple = (c.date, c.account, c.symbol, c.long_term, c.currency)
        name, legs = label(c.symbol), 1
        if c.kind == "option" and (m := _OCC.match(c.symbol.replace(" ", ""))):
            pos = (c.account, c.underlying or m["root"], f"20{m['y']}-{m['m']}-{m['d']}")
            if len(spread[pos]) > 1:
                key = ("", c.account, f"{pos[1]} {pos[2]}", False, c.currency)
                name, legs = f"{pos[1]} {pos[2]}", len(spread[pos])
        g = grouped.setdefault(key, [ZERO, ZERO, ZERO, ZERO, c.date, name, legs])
        g[0] += Decimal(c.quantity)
        g[1] += Decimal(c.cost)
        g[2] += Decimal(c.proceeds)
        g[3] += Decimal(c.wash or 0)
        g[4] = max(g[4], c.date)
    sales = [
        Sale(
            date=last,
            account=account,
            symbol=symbol,
            name=name,
            quantity=_num(qty),
            cost=_q(cost),
            proceeds=_q(proceeds),
            gain=_q(proceeds - cost),
            long_term=long_term,
            currency=ccy,
            legs=legs,
            wash=_q(wash) if wash else None,
        )
        for (_, account, symbol, long_term, ccy), (
            qty,
            cost,
            proceeds,
            wash,
            last,
            name,
            legs,
        ) in grouped.items()
    ]
    sales.sort(key=lambda x: (x.date, x.symbol), reverse=True)
    closed.sort(key=lambda c: (c.date, c.symbol), reverse=True)
    unmatched.sort(key=lambda u: (u.date, u.symbol), reverse=True)
    return Realized(
        currency=currency,
        by_year={y: _q(v) for y, v in sorted(by_year.items(), reverse=True)},
        tax={
            y: TaxYear(_q(v[0]), _q(v[1]), _q(v[2]))
            for y, v in sorted(tax_sums.items(), reverse=True)
        },
        sales=sales,
        closed=closed,
        unmatched=unmatched,
        missing_fx=sorted(fx.missing),
    )


NOT_LISTED = {"option", "crypto", "cryptocurrency", "coin", "future", "forex"}
WASH_DAYS = 30


def _wash_sales(closed: list[Closed], rows: Sequence[Any], cash: set[tuple[int, str]]) -> None:
    """Mark the losses a wash sale would disallow: a share sold at a loss while the same symbol
    was bought within 30 days before or after, in any account (the purchase that opened the lot
    itself does not count). The disallowed part is the loss on as many shares as were bought
    back. Shares and funds only; a broker's own lot matching may differ, so this is an estimate."""
    buys: dict[str, list[tuple[dt.date, Decimal, int]]] = defaultdict(list)
    for r in rows:
        if r.action in ("BUY", "REINVEST") and r.kind != "option" and not _is_cash(r, cash):
            buys[r.symbol].append((dt.date.fromisoformat(r.date), abs(Decimal(r.quantity)), r.id))
    for c in closed:
        loss = -Decimal(c.gain)
        if loss <= 0 or c.kind == "option":
            continue
        sold_on, qty = dt.date.fromisoformat(c.date), Decimal(c.quantity)
        back = sum(
            (
                q
                for day, q, tid in buys.get(c.symbol, [])
                if tid != c.opened_id and abs((day - sold_on).days) <= WASH_DAYS
            ),
            ZERO,
        )
        if back > 0 and qty > 0:
            c.wash = _q(loss * min(Decimal(1), back / qty))


# ---------------------------------------------------------------- performance
@dataclass
class Versus:
    """The same dollars put into an index on the same days (and taken out on the same days)."""

    ticker: str
    value: str  # what that would be worth now
    gain: str
    rate: float | None  # annualised, money-weighted


@dataclass
class Performance:
    currency: str
    since: str | None  # the first trade the broker's history has
    as_of: str | None
    invested: str  # money put into positions
    returned: str  # money taken back out by sales
    value: str  # what is still held, at the latest prices
    gain: str  # value + returned - invested: realised and unrealised together
    rate: float | None  # annualised, money-weighted (what a bank would call the yield)
    dividends: dict[str, str]  # by year, as booked in the ledger
    versus: list[Versus] = field(default_factory=list)
    note: str | None = None  # why a comparison is missing


def _xirr(flows: list[tuple[dt.date, float]]) -> float | None:
    """Annualised money-weighted return of dated flows (- = paid in, + = received)."""
    if len(flows) < 2 or not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    t0 = min(d for d, _ in flows)
    span = max((d - t0).days for d, _ in flows)
    if span < 30:
        return None  # a few weeks annualised is a number that means nothing

    def npv(rate: float) -> float:
        return sum(a / (1 + rate) ** ((d - t0).days / 365.25) for d, a in flows)

    lo, hi = -0.9999, 100.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 4)


def _price_on(closes: dict[str, float], day: str) -> float | None:
    """The close on `day`, or the last one before it (a weekend, a holiday)."""
    if day in closes:
        return closes[day]
    earlier = [d for d in closes if d <= day]
    return closes[max(earlier)] if earlier else None


def performance(
    conn: Connection,
    currency: str = "USD",
    benchmarks: tuple[str, ...] = ("QQQ", "SPY"),
    closes=None,  # type: ignore[no-untyped-def]
) -> Performance:
    """What the money put into stocks, funds and options has done, and what the same money would
    have done in an index.

    Every buy is money in and every sale money out, on its day (cash sweeps left out). What is
    still held is valued at the newest broker report, counting only what the trades explain:
    shares bought before the broker's history starts have no purchase to compare with. The
    index figure replays the same flows: each buy buys that many dollars of the index that day,
    each sale sells that many, and what is left is valued today. Dividends are listed apart, as
    the ledger booked them (an index's closes are total-return, so its dividends are inside)."""
    from pf.market.providers.prices import daily_closes

    closes = closes or daily_closes
    fx = Fx(conn, currency)
    cash = _cash_symbols(conn)
    rows = conn.execute(text("SELECT t.* FROM trades t ORDER BY t.date, t.id")).all()
    flows: list[tuple[dt.date, float]] = []
    invested = returned = ZERO
    held: dict[tuple[int, str], Decimal] = defaultdict(lambda: ZERO)  # quantity the trades explain
    for r in rows:
        if _is_cash(r, cash):
            continue
        qty, amount = Decimal(r.quantity), Decimal(r.amount) * (_rate(fx, r.currency, r.date))
        key = (r.account_id, r.symbol)
        if r.action in OPENING_SIGN:
            held[key] += OPENING_SIGN[r.action] * abs(qty)
        elif r.action in ("SPLIT", "STOCK_DIVIDEND"):
            held[key] += qty
        elif r.action in CLOSING:
            held[key] = ZERO
        if amount < 0:
            invested += -amount
        elif amount > 0:
            returned += amount
        if amount:
            flows.append((dt.date.fromisoformat(r.date), float(amount)))

    now = positions(conn, None, currency)
    value = ZERO
    for acc in now.accounts:
        acc_id = int(
            conn.execute(
                text("SELECT id FROM accounts WHERE name = :n"), {"n": acc.account}
            ).scalar_one()
        )
        for p in acc.positions:
            if p.cash_equivalent or not p.price:
                continue
            explained = min(Decimal(p.quantity), max(held.get((acc_id, p.symbol), ZERO), ZERO))
            value += explained * Decimal(p.price) * (_rate(fx, p.currency, acc.as_of))
    as_of = now.as_of or (rows[-1].date if rows else None)

    dividends: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in conn.execute(
        text(
            """
            SELECT vp.date, vp.amount, vp.commodity FROM v_postings vp
            WHERE (vp.account = :div OR vp.account LIKE :div || ':%')
              AND EXISTS (SELECT 1 FROM v_postings o JOIN accounts a ON a.id = o.account_id
                           WHERE o.txn_id = vp.txn_id AND a.asset_class IN ('brokerage', 'retirement'))
            """
        ),
        {"div": DIVIDENDS},
    ):
        dividends[r.date[:4]] += Decimal(-fx.to_base(r.amount, r.commodity, r.date)) / 100

    out = Performance(
        currency=currency,
        since=rows[0].date if rows else None,
        as_of=as_of,
        invested=_q(invested),
        returned=_q(returned),
        value=_q(value),
        gain=_q(value + returned - invested),
        rate=None,
        dividends={y: _q(v) for y, v in sorted(dividends.items(), reverse=True)},
    )
    if not flows or as_of is None:
        return out
    end = dt.date.fromisoformat(as_of)
    out.rate = _xirr([*flows, (end, float(value))])
    start = min(d for d, _ in flows)
    for ticker in benchmarks:
        try:
            series = closes(ticker, start)
        except Exception:
            out.note = "index prices could not be fetched"
            continue
        last = _price_on(series, as_of)
        units = 0.0
        ok = last is not None
        for day, amount in flows:
            px = _price_on(series, day.isoformat())
            if px is None:
                ok = False
                break
            units += -amount / px  # money in buys units, money out sells them
        if not ok or last is None:
            out.note = "index prices do not cover the whole period"
            continue
        worth = Decimal(str(round(units * last, 2)))
        out.versus.append(
            Versus(
                ticker=ticker,
                value=_q(worth),
                gain=_q(worth + returned - invested),
                rate=_xirr([*flows, (end, float(worth))]),
            )
        )
    return out


@dataclass
class SymbolHistory:
    symbol: str
    closes: list[tuple[str, float]]  # (day, close), split-adjusted
    trades: list[Trade]
    note: str | None = None


def symbol_history(conn: Connection, symbol: str, closes=None) -> SymbolHistory:  # type: ignore[no-untyped-def]
    """One holding from its first trade to now: daily closes and where it was bought and sold."""
    from pf.market.providers.prices import daily_closes

    symbol = symbol.upper()
    mine = [t for t in trades(conn, symbol=symbol) if t.symbol == symbol]
    start = dt.date.fromisoformat(min((t.date for t in mine), default=dt.date.today().isoformat()))
    start = min(start, dt.date.today() - dt.timedelta(days=90)) - dt.timedelta(days=14)
    try:
        series = (closes or daily_closes)(symbol, start)
    except Exception:
        return SymbolHistory(symbol, [], mine, "prices could not be fetched")
    return SymbolHistory(symbol, sorted(series.items()), mine)


# ---------------------------------------------------------------- positions before the reports
def _held_before_first_report(
    conn: Connection, account: str, institution: str | None, end: dt.date
) -> list[dict[str, Any]]:
    """Quantities held on `end` for an account whose first broker report is later: that first
    report with every trade after `end` undone. Shares and funds only (an option's closing
    events do not say which way the position faced); no prices, so no values."""
    first = conn.execute(
        text(
            # the first report that lists anything: an empty one from a connection still
            # warming up says nothing, and later trades are undone either way
            "SELECT s.account_id, MIN(s.as_of) AS as_of FROM holdings s"
            " JOIN accounts a ON a.id = s.account_id WHERE a.name = :n AND s.as_of > :e"
        ),
        {"n": account, "e": end.isoformat()},
    ).first()
    if first is None or first.as_of is None:
        return []
    qty: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in _rows(conn, first.account_id, first.as_of).values():
        if r.kind != "option":
            qty[r.symbol] = Decimal(r.quantity)
    undo = conn.execute(
        text(
            "SELECT action, symbol, quantity FROM trades WHERE account_id = :a AND date > :e"
            " AND date <= :d AND COALESCE(kind, '') != 'option'"
        ),
        {"a": first.account_id, "e": end.isoformat(), "d": first.as_of},
    )
    for t in undo:
        q = Decimal(t.quantity)
        if t.action in OPENING_SIGN:
            qty[t.symbol] -= OPENING_SIGN[t.action] * abs(q)
        elif t.action in ("SPLIT", "STOCK_DIVIDEND"):
            qty[t.symbol] -= q
    return [
        {
            "account": account,
            "institution": institution,
            "symbol": symbol,
            "name": label(symbol),
            "quantity": _num(q),
            "from_report": first.as_of,
        }
        for symbol, q in sorted(qty.items())
        if q > 0
    ]


# ---------------------------------------------------------------- statement
@dataclass
class StatementAccount:
    account: str
    institution: str | None
    number_ending: str | None
    first_activity: str | None  # the earliest day Keel knows of, not the day it was opened
    value: str | None  # on the report day, in the account's currency
    currency: str | None


def statement(
    conn: Connection, start: dt.date, end: dt.date, currency: str = "USD"
) -> dict[str, Any]:
    """Brokerage and exchange accounts, what they held at `end` and what was traded from
    `start` to `end`: the facts a brokerage-account certification asks for. Dates of opening
    and full account numbers are the broker's to state; Keel only has what the feed carries."""
    accounts = []
    for a in conn.execute(
        text(
            "SELECT id, name, institution, currency FROM accounts"
            " WHERE asset_class IN ('brokerage', 'retirement', 'crypto') ORDER BY name"
        )
    ):
        leaf = a.name.rsplit(":", 1)[-1]
        digits = leaf.rsplit("-", 1)[1] if "-" in leaf else None
        first = conn.execute(
            text(
                "SELECT MIN(d) FROM (SELECT MIN(date) AS d FROM trades WHERE account_id = :a"
                " UNION ALL SELECT MIN(t.date) FROM transactions t JOIN postings p"
                " ON p.txn_id = t.id WHERE p.account_id = :a AND t.kind != 'opening')"
            ),
            {"a": a.id},
        ).scalar()
        mark = conn.execute(
            text(
                "SELECT value, commodity FROM valuations WHERE account_name = :n AND date <= :e"
                " ORDER BY date DESC LIMIT 1"
            ),
            {"n": a.name, "e": end.isoformat()},
        ).first()
        prec = conn.execute(
            text("SELECT precision FROM commodities WHERE code = :c"),
            {"c": mark.commodity if mark else a.currency or "USD"},
        ).scalar()
        accounts.append(
            StatementAccount(
                account=a.name,
                institution=a.institution,
                number_ending=digits if digits and digits.isdigit() else None,
                first_activity=first,
                value=_q(Decimal(mark.value) / (10 ** (prec or 2))) if mark else None,
                currency=mark.commodity if mark else a.currency,
            )
        )
    held = positions(conn, end, currency)
    reported = {a.account for a in held.accounts}
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "currency": currency,
        "accounts": [asdict(a) for a in accounts],
        "holdings": asdict(held),
        "holdings_from_trades": [
            row
            for a in accounts
            if a.account not in reported
            for row in _held_before_first_report(conn, a.account, a.institution, end)
        ],
        "trades": [asdict(t) for t in trades(conn, start=start, end=end)],
        "cash_movements": len(trades(conn, start=start, end=end, include_cash=True))
        - len(trades(conn, start=start, end=end)),
    }
