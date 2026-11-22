"""Mark-to-market for accounts without full holdings data (brokerage you don't import, home, car).

A valuation is an ABSOLUTE mark ("on 2026-09-30 this account was worth X"), stored in
`valuations` as a user decision. The balancing adjustment transactions (other leg:
Income:Investment:Unrealized) are always regenerated from the marks in date order, so editing an
old mark, importing more history or re-categorising contributions can never leave them stale.
Adjustments are excluded from the cash-flow statement but count in net worth and returns.

Marks imported from a statement (source='statement': broker positions, IBKR net asset value) also
set the account's starting point: the first one's difference from the booked flows is what the
account already held, so it is booked against Equity:Opening-Balances before the account's first
flow instead of as a gain (unless statement balance assertions already derive an opening)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Connection, text

from pf.ledger.core import OPENING_BALANCES, NewPosting, NewTxn, delete_txn, ensure_account, post
from pf.money import Commodities, from_minor, to_minor

UNREALIZED = "Income:Investment:Unrealized"


def set_value(
    conn: Connection, account: str, date: dt.date, value: Decimal, commodity: str = "USD"
) -> None:
    ensure_account(conn, account, currency=commodity)
    prec = Commodities(conn).precision(commodity, "currency")
    conn.execute(
        text(
            "INSERT INTO valuations(account_name, date, value, commodity) VALUES (:a, :d, :v, :c)"
            " ON CONFLICT(account_name, date, commodity)"
            " DO UPDATE SET value = excluded.value, source = 'user'"
        ),
        {"a": account, "d": date.isoformat(), "v": to_minor(value, prec), "c": commodity},
    )
    refresh_valuations(conn, account)


def delete_value(conn: Connection, account: str, date: dt.date) -> None:
    conn.execute(
        text("DELETE FROM valuations WHERE account_name = :a AND date = :d"),
        {"a": account, "d": date.isoformat()},
    )
    refresh_valuations(conn, account)


def refresh_valuations(conn: Connection, account: str | None = None) -> None:
    """Drop and regenerate adjustment transactions for one account (or all valued accounts)."""
    names = (
        [account]
        if account
        else [r[0] for r in conn.execute(text("SELECT DISTINCT account_name FROM valuations"))]
    )
    for name in names:
        acc = conn.execute(text("SELECT id FROM accounts WHERE name = :n"), {"n": name}).first()
        if acc is None:
            continue
        for (tid,) in conn.execute(
            text(
                "SELECT DISTINCT t.id FROM transactions t JOIN postings p ON p.txn_id = t.id"
                " WHERE t.kind = 'adjustment' AND p.account_id = :a"
            ),
            {"a": acc.id},
        ).all():
            delete_txn(conn, tid)
        marks = conn.execute(
            text(
                "SELECT date, value, commodity, source FROM valuations WHERE account_name = :n"
                " ORDER BY date"
            ),
            {"n": name},
        ).all()
        comms = Commodities(conn)
        has_opening = conn.execute(
            text("SELECT 1 FROM balance_assertions WHERE account_id = :a LIMIT 1"), {"a": acc.id}
        ).first()
        for i, m in enumerate(marks):
            booked = conn.execute(
                text(
                    "SELECT COALESCE(SUM(amount), 0) FROM v_postings"
                    " WHERE account_id = :a AND commodity = :c AND date <= :d"
                ),
                {"a": acc.id, "c": m.commodity, "d": m.date},
            ).scalar_one()
            diff = int(m.value) - int(booked)
            if diff == 0:
                continue
            amount = from_minor(diff, comms.precision(m.commodity, "currency"))
            date, desc, other = dt.date.fromisoformat(m.date), f"Valuation {name}", UNREALIZED
            if i == 0 and m.source == "statement" and not has_opening:
                earliest = conn.execute(
                    text("SELECT MIN(date) FROM v_postings WHERE account_id = :a"), {"a": acc.id}
                ).scalar_one()
                date = min(date, dt.date.fromisoformat(earliest or m.date)) - dt.timedelta(days=1)
                desc, other = f"Opening balance {name}", OPENING_BALANCES
            post(
                conn,
                NewTxn(
                    date=date,
                    description=desc,
                    kind="adjustment",
                    status="confirmed",
                    weight_commodity=m.commodity,
                    postings=[
                        NewPosting(name, amount, m.commodity),
                        NewPosting(other, -amount, m.commodity),
                    ],
                ),
            )


def value_of(conn: Connection, account: str, date: str) -> Decimal:
    v = conn.execute(
        text(
            "SELECT COALESCE(SUM(amount),0), MAX(precision) FROM v_postings WHERE account = :a AND date <= :d"
        ),
        {"a": account, "d": date},
    ).one()
    return from_minor(int(v[0]), int(v[1] or 2))
