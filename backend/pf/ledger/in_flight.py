"""In flight: what the bank shows but has not posted yet. Shown beside the books, never in them.

A connection reports, with every sync, the lines the bank still lists as pending (a card swipe,
a hotel deposit, an incoming transfer) and, for a cash account, its current and available
balance; the difference is money on its way in, or held. None of it is booked: net worth,
reports, reconciliation and month close count posted lines only, so they always agree with the
bank. When a line posts it arrives as an ordinary transaction and drops out of the pending list;
a released hold simply disappears.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from pf.importers.base import ParsedStatement
from pf.reports.common import Fx

STALE_DAYS = 10  # a connection that stopped reporting: its old pending lines are not shown


def record(conn: Connection, account_id: int, stmt: ParsedStatement) -> None:
    """Replace the account's in-flight picture with the one in this statement."""
    as_of = stmt.in_flight_as_of or dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    if stmt.bank_pending is not None:
        conn.execute(text("DELETE FROM bank_pending WHERE account_id = :a"), {"a": account_id})
        for line in stmt.bank_pending:
            conn.execute(
                text(
                    "INSERT OR REPLACE INTO bank_pending(account_id, ref, date, amount, currency,"
                    " description, payee, category_hint, kind, hold, as_of)"
                    " VALUES (:a, :ref, :date, :amount, :cur, :desc, :payee, :hint, :kind,"
                    " :hold, :as_of)"
                ),
                {
                    "a": account_id,
                    "ref": str(line["id"]),
                    "date": line["date"],
                    "amount": str(Decimal(str(line["amount"]))),
                    "cur": stmt.currency,
                    "desc": line.get("description") or "",
                    "payee": line.get("payee"),
                    "hint": line.get("category_hint"),
                    "kind": line.get("kind") or "spend",
                    "hold": 1 if line.get("hold") else 0,
                    "as_of": as_of,
                },
            )
    if stmt.bank_current is not None and stmt.bank_available is not None:
        conn.execute(
            text(
                "INSERT INTO bank_available(account_id, as_of, current, available)"
                " VALUES (:a, :as_of, :c, :v) ON CONFLICT(account_id) DO UPDATE SET"
                " as_of = excluded.as_of, current = excluded.current,"
                " available = excluded.available"
            ),
            {
                "a": account_id,
                "as_of": as_of,
                "c": str(stmt.bank_current),
                "v": str(stmt.bank_available),
            },
        )


@dataclass
class InFlightLine:
    account: str
    date: str
    amount: str  # native, ledger sign
    currency: str
    description: str
    payee: str | None
    kind: str  # spend | income | transfer
    hold: bool
    shown: str | None  # in the requested currency, None when there is no rate


@dataclass
class InFlightAccount:
    account: str
    currency: str
    current: str
    available: str
    difference: str  # available - current: + on its way in, - held
    as_of: str


@dataclass
class InFlight:
    currency: str
    lines: list[InFlightLine] = field(default_factory=list)
    accounts: list[InFlightAccount] = field(default_factory=list)
    spend: str = "0"  # pending spending, positive, in `currency`
    spend_count: int = 0
    holds: int = 0
    incoming: str = "0"  # sum of positive available-minus-current gaps, in `currency`
    missing_fx: list[str] = field(default_factory=list)


def in_flight(conn: Connection, currency: str = "USD", today: dt.date | None = None) -> InFlight:
    today = today or dt.date.today()
    cutoff = (today - dt.timedelta(days=STALE_DAYS)).isoformat()
    fx = Fx(conn, currency)
    out = InFlight(currency=currency)
    missing: set[str] = set()

    def shown(amount: Decimal, cur: str) -> Decimal | None:
        rate = fx.rate(cur, today.isoformat())
        if rate is None:
            missing.add(cur)
            return None
        return amount * rate

    spend = Decimal(0)
    for r in conn.execute(
        text(
            "SELECT a.name AS account, p.* FROM bank_pending p JOIN accounts a ON a.id = p.account_id"
            " WHERE p.as_of >= :cut ORDER BY p.date DESC, p.id"
        ),
        {"cut": cutoff},
    ):
        amount = Decimal(r.amount)
        value = shown(amount, r.currency)
        out.lines.append(
            InFlightLine(
                account=r.account,
                date=r.date,
                amount=str(amount),
                currency=r.currency,
                description=r.description,
                payee=r.payee,
                kind=r.kind,
                hold=bool(r.hold),
                shown=None if value is None else str(value.quantize(Decimal("0.01"))),
            )
        )
        if r.kind == "spend" and value is not None:
            spend -= value
            out.spend_count += 1
            out.holds += 1 if r.hold else 0
    incoming = Decimal(0)
    for r in conn.execute(
        text(
            "SELECT a.name AS account, a.currency AS currency, b.*"
            " FROM bank_available b JOIN accounts a ON a.id = b.account_id"
            " WHERE b.as_of >= :cut AND a.type = 'Assets' ORDER BY a.name"
        ),
        {"cut": cutoff},
    ):
        diff = Decimal(r.available) - Decimal(r.current)
        if diff == 0:
            continue
        out.accounts.append(
            InFlightAccount(
                account=r.account,
                currency=r.currency,
                current=r.current,
                available=r.available,
                difference=str(diff),
                as_of=r.as_of,
            )
        )
        if diff > 0 and (value := shown(diff, r.currency)) is not None:
            incoming += value
    out.spend = str(spend.quantize(Decimal("0.01")))
    out.incoming = str(incoming.quantize(Decimal("0.01")))
    out.missing_fx = sorted(missing)
    return out


def as_dict(f: InFlight) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(f)
