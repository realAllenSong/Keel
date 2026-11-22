"""Check every balance assertion (statement balance) against what the ledger has booked.

A statement's closing balance must match exactly on its date. A balance a connector (Plaid,
SnapTrade…) pulled is a snapshot of what had *posted* when it synced, while the bank dates each
line by when it happened, often a day or two earlier. So a connector snapshot counts as matched
when the ledger held that balance on any day within ``SNAPSHOT_SLACK_DAYS`` of it.

The newest snapshots can be ahead of the lines themselves (the balance moved, the line arrives
tomorrow): for ``SETTLE_DAYS`` such a gap is "waiting for sync", shown to nobody as a problem.
A truly missing line never closes the gap: once the gap is older than that it is reported, and
stays reported.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import Connection, text

from pf.money import from_minor

SNAPSHOT_SLACK_DAYS = 5
# A bank moves the balance first and lists the line a day or two later. A snapshot that stopped
# matching this recently is waiting for those lines, not a hole in the ledger.
SETTLE_DAYS = 4
# A card payment Keel booked from the bank's side (pf/ledger/transfers.py, "in transit") can take
# this long to show on the card.
TRANSIT_DAYS = 21


@dataclass
class Discrepancy:
    account: str
    date: str
    commodity: str
    statement: str  # balance per statement
    ledger: str  # balance per ledger
    diff: str  # ledger - statement
    waiting: bool = False  # a fresh connector snapshot whose lines have not synced yet


def reconcile(conn: Connection, account: str | None = None) -> list[Discrepancy]:
    """Balances the ledger cannot explain. Snapshots still waiting for their lines are left
    out (see `waiting_for_sync`): they need nothing from the user."""
    return [d for d in check(conn, account) if not d.waiting]


def waiting_for_sync(conn: Connection, account: str | None = None) -> list[Discrepancy]:
    return [d for d in check(conn, account) if d.waiting]


def check(
    conn: Connection, account: str | None = None, *, today: dt.date | None = None
) -> list[Discrepancy]:
    today = today or dt.date.today()
    rows = conn.execute(
        text(
            """
            SELECT a.name AS account, b.date, b.commodity, c.precision, b.amount AS expected,
                   (SELECT COALESCE(SUM(vp.amount), 0) FROM v_postings vp
                     WHERE vp.account_id = b.account_id AND vp.commodity = b.commodity
                       AND vp.date <= b.date) AS actual,
                   b.account_id, MAX(COALESCE(f.importer = 'connector', 0)) AS snapshot
            FROM balance_assertions b
            JOIN accounts a ON a.id = b.account_id
            LEFT JOIN import_files f ON f.id = b.import_file_id
            JOIN commodities c ON c.code = b.commodity
            WHERE (:acc IS NULL OR a.name = :acc)
            GROUP BY b.account_id, b.date, b.commodity
            ORDER BY a.name, b.commodity, b.date
            """
        ),
        {"acc": account},
    ).all()
    out: list[Discrepancy] = []
    streak: dict[tuple[int, str], list[Discrepancy]] = {}  # unmatched snapshots in a row
    for r in rows:
        key = (r.account_id, r.commodity)
        matched = r.expected == r.actual or (
            r.snapshot
            and (
                _held_nearby(conn, r.account_id, r.commodity, r.date, r.expected)
                or _in_transit(conn, r.account_id, r.commodity, r.date) == r.actual - r.expected
            )
        )
        if matched:
            streak.pop(key, None)
            continue
        d = Discrepancy(
            account=r.account,
            date=r.date,
            commodity=r.commodity,
            statement=str(from_minor(r.expected, r.precision)),
            ledger=str(from_minor(r.actual, r.precision)),
            diff=str(from_minor(r.actual - r.expected, r.precision)),
        )
        out.append(d)
        if r.snapshot:
            streak.setdefault(key, []).append(d)
        else:
            streak.pop(key, None)  # a statement's own balance is never "still syncing"
    for run in streak.values():
        # The run reaches today's side of the ledger: waiting only while it is young. A gap
        # that began longer ago has had its chance, and stays reported day after day.
        began = dt.date.fromisoformat(run[0].date)
        if (today - began).days <= SETTLE_DAYS:
            for d in run:
                d.waiting = True
    return out


def _in_transit(conn: Connection, account_id: int, commodity: str, date: str) -> int | None:
    """Card payments booked from the bank's side that this account has not listed yet: the
    ledger is ahead of the card's own balance by exactly this much."""
    lo = (dt.date.fromisoformat(date) - dt.timedelta(days=TRANSIT_DAYS)).isoformat()
    total = conn.execute(
        text(
            "SELECT SUM(p.amount) FROM postings p JOIN transactions t ON t.id = p.txn_id"
            " WHERE p.account_id = :a AND p.commodity = :c AND p.source_key IS NULL"
            " AND t.category_source = 'transit' AND t.date > :lo AND t.date <= :d"
        ),
        {"a": account_id, "c": commodity, "lo": lo, "d": date},
    ).scalar()
    return int(total) if total is not None else None


def _held_nearby(conn: Connection, account_id: int, commodity: str, date: str, amount: int) -> bool:
    """Did the ledger balance equal ``amount`` on some day within the slack window of ``date``?"""
    d = dt.date.fromisoformat(date)
    lo = (d - dt.timedelta(days=SNAPSHOT_SLACK_DAYS)).isoformat()
    hi = (d + dt.timedelta(days=SNAPSHOT_SLACK_DAYS)).isoformat()
    bal = conn.execute(
        text(
            "SELECT COALESCE(SUM(amount), 0) FROM v_postings"
            " WHERE account_id = :a AND commodity = :c AND date <= :lo"
        ),
        {"a": account_id, "c": commodity, "lo": lo},
    ).scalar_one()
    if bal == amount:
        return True
    moves = conn.execute(
        text(
            "SELECT date, SUM(amount) FROM v_postings"
            " WHERE account_id = :a AND commodity = :c AND date > :lo AND date <= :hi"
            " GROUP BY date ORDER BY date"
        ),
        {"a": account_id, "c": commodity, "lo": lo, "hi": hi},
    ).all()
    for _, delta in moves:
        bal += delta
        if bal == amount:
            return True
    return False
