"""Pair the two halves of an internal transfer (card payment, account-to-account, top-ups,
FX) into ONE transaction, so neither side is counted as income or spending."""

from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import Connection, text

from pf.ledger.core import NewPosting, NewTxn, delete_txn, post, recategorize
from pf.money import from_minor

MAX_DAYS = 5
TRANSFER_WORDS = re.compile(
    r"payment|\bpmt\b|autopay|transfer|xfer|epay|thank you|zelle to self|online banking|"
    r"转账|还款|充值|提现|转入|转出",
    re.I,
)

# Statement leg of every imported 2-leg transaction not yet confirmed by you (uncategorised or
# only *suggested*): a pending half can still turn out to be one side of a transfer.
_CANDIDATES = """
SELECT t.id AS txn_id, t.date, t.description, t.category_source, t.import_file_id, p.account_id,
       a.name AS account, p.amount, p.commodity, c.precision, p.source_key
FROM transactions t
JOIN postings p ON p.txn_id = t.id AND p.source_key IS NOT NULL
JOIN accounts a ON a.id = p.account_id AND a.type IN ('Assets', 'Liabilities')
JOIN commodities c ON c.code = p.commodity
JOIN postings q ON q.txn_id = t.id AND q.id != p.id
JOIN accounts qa ON qa.id = q.account_id AND qa.type IN ('Expenses', 'Income')
WHERE t.sealed = 1 AND t.kind = 'normal'
  -- not yet decided by you: pending, accepted automatically from a suggestion, or filed by a
  -- rule (a broad rule like "american" -> flights must not hide an "AMERICAN EXPRESS ACH PMT"
  -- card payment; rule-filed lines pair only when one side reads like a payment, see below)
  AND (t.status = 'pending' OR t.category_source IN ('hint', 'learned', 'rule', 'seed'))
  AND (SELECT COUNT(*) FROM postings WHERE txn_id = t.id) = 2
"""


# A card payment seen only from the bank's side so far (see `book_card_payments`), waiting for
# the card to list it.
TRANSIT_DAYS = 21
PAYMENT_WORDS = re.compile(r"payment|\bpmt\b|autopay|epay|crd\b|card|还款", re.I)
ISSUER_ALIASES = {
    "american express": ("american express", "amex"),
    "bank of america": ("bank of america", "bofa", "bk of amer"),
    "capital one": ("capital one", "capitalone"),
    "citi": ("citi", "citibank"),
}

_TRANSIT = """
SELECT t.id AS txn_id, t.date, t.description, t.import_file_id, p.account_id,
       a.name AS account, p.amount, p.commodity, c.precision, p.source_key,
       q.account_id AS partner_id
FROM transactions t
JOIN postings p ON p.txn_id = t.id AND p.source_key IS NOT NULL
JOIN accounts a ON a.id = p.account_id
JOIN commodities c ON c.code = p.commodity
JOIN postings q ON q.txn_id = t.id AND q.id != p.id AND q.source_key IS NULL
WHERE t.category_source = 'transit' AND t.kind = 'transfer'
"""


def book_card_payments(conn: Connection, txn_ids: list[int]) -> list[int]:
    """A bank line that pays one of your cards, booked as a transfer to that card at once.

    The bank lists the payment the day it leaves; the card shows it days later. Until then the
    ledger would count the money twice: gone from the bank and still owed on the card (net worth
    drops by the payment, and it reads as spending). So when a new bank outflow reads like a
    card payment, names the issuer of a card you hold, and that card owes it, the line becomes a
    transfer to the card ("in transit"). When the card's own line arrives, `match_transfers`
    merges the two into one ordinary paired transfer.

    Only cards a connector keeps current are eligible: their side will arrive within days."""
    if not txn_ids:
        return []
    import json

    cards = conn.execute(
        text(
            "SELECT a.id, a.name, a.institution FROM accounts a WHERE a.type = 'Liabilities'"
            " AND a.institution IS NOT NULL AND EXISTS (SELECT 1 FROM balance_assertions b"
            " JOIN import_files f ON f.id = b.import_file_id AND f.importer = 'connector'"
            " WHERE b.account_id = a.id)"
        )
    ).all()
    if not cards:
        return []
    rows = conn.execute(
        text(
            _CANDIDATES + " AND a.type = 'Assets' AND p.amount < 0 AND t.status = 'pending'"
            " AND t.id IN (SELECT value FROM json_each(:ids))"
            " AND NOT EXISTS (SELECT 1 FROM overrides o WHERE o.account_name = a.name"
            "                 AND o.source_key = p.source_key)"
            " ORDER BY t.date, p.source_key"
        ),
        {"ids": json.dumps(txn_ids)},
    ).all()
    done: list[int] = []
    for r in rows:
        if not PAYMENT_WORDS.search(r.description):
            continue
        text_ = " ".join(r.description.lower().split())
        named = [
            c
            for c in cards
            if c.id != r.account_id
            and any(
                re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text_)
                for w in ISSUER_ALIASES.get(c.institution.lower(), (c.institution.lower(),))
            )
        ]
        card = _card_that_owes(conn, named, -r.amount, r.commodity, r.date)
        if card is None:
            continue
        other = conn.execute(
            text("SELECT id FROM postings WHERE txn_id = :t AND source_key IS NULL"),
            {"t": r.txn_id},
        ).scalar_one()
        recategorize(conn, other, card.name, payee=f"Transfer → {card.name}")
        conn.execute(
            text(
                "UPDATE transactions SET kind = 'transfer', status = 'confirmed',"
                " category_source = 'transit' WHERE id = :t"
            ),
            {"t": r.txn_id},
        )
        done.append(r.txn_id)
    return done


def _card_that_owes(conn: Connection, cards: list, amount: int, commodity: str, date: str):  # type: ignore[no-untyped-def]
    """The one card this payment is for: the card whose balance (now, or on a statement or
    sync of the last weeks) is exactly the amount; with a single card at the issuer, a card
    that owes at least that much. None when it could be more than one."""
    if not cards:
        return None
    since = (dt.date.fromisoformat(date) - dt.timedelta(days=45)).isoformat()
    exact, owing = [], []
    for c in cards:
        debt = -conn.execute(
            text(
                "SELECT COALESCE(SUM(amount), 0) FROM v_postings WHERE account_id = :a"
                " AND commodity = :c AND date <= :d"
            ),
            {"a": c.id, "c": commodity, "d": date},
        ).scalar_one()
        stated = conn.execute(
            text(
                "SELECT 1 FROM balance_assertions WHERE account_id = :a AND commodity = :c"
                " AND date > :s AND date <= :d AND amount = :owed LIMIT 1"
            ),
            {"a": c.id, "c": commodity, "s": since, "d": date, "owed": -amount},
        ).first()
        if debt == amount or stated:
            exact.append(c)
        if debt >= amount:
            owing.append(c)
    if len(exact) == 1:
        return exact[0]
    if not exact and len(cards) == 1 and owing:
        return cards[0]
    return None


def _by_rule(row) -> bool:  # type: ignore[no-untyped-def]
    return row.category_source in ("rule", "seed")


def match_transfers(conn: Connection, txn_ids: list[int]) -> list[int]:
    """Try to pair each new transaction with an existing opposite one. Returns new txn ids."""
    if not txn_ids:
        return []
    created: list[int] = []
    new = set(txn_ids)
    # Deterministic order: by date, then source_key — import order must not change results.
    rows = conn.execute(text(_CANDIDATES + " ORDER BY t.date, p.source_key")).all()
    alive = {r.txn_id: r for r in rows}
    transit = {r.txn_id: r for r in conn.execute(text(_TRANSIT + " ORDER BY t.date, t.id"))}
    for r in rows:
        if r.txn_id not in new or r.txn_id not in alive:
            continue
        d = dt.date.fromisoformat(r.date)
        # First: is this the card's side of a payment already booked from the bank's side?
        waiting = [
            o
            for o in transit.values()
            if o.partner_id == r.account_id
            and o.commodity == r.commodity
            and o.amount == -r.amount
            and abs((dt.date.fromisoformat(o.date) - d).days) <= TRANSIT_DAYS
        ]
        if waiting:
            o = min(
                waiting, key=lambda o: (abs((dt.date.fromisoformat(o.date) - d).days), o.txn_id)
            )
            for t in (r, o):
                delete_txn(conn, t.txn_id)
            alive.pop(r.txn_id, None)
            transit.pop(o.txn_id)
            created.append(_paired(conn, o, r, confident=True))
            continue
        best = None
        for o in alive.values():
            if (
                o.txn_id == r.txn_id
                or o.account_id == r.account_id
                or o.commodity != r.commodity
                or o.amount != -r.amount
            ):
                continue
            gap = abs((dt.date.fromisoformat(o.date) - d).days)
            if gap > MAX_DAYS:
                continue
            key = (gap, o.source_key)
            if best is None or key < best[0]:
                best = (key, o)
        if best is None:
            continue
        o = best[1]
        out, inn = (r, o) if r.amount < 0 else (o, r)
        confident = bool(
            TRANSFER_WORDS.search(r.description) or TRANSFER_WORDS.search(o.description)
        )
        if not confident and (_by_rule(r) or _by_rule(o)):
            continue  # a rule's category stands unless the pair is clearly a payment
        for t in (r, o):
            delete_txn(conn, t.txn_id)
            alive.pop(t.txn_id, None)
        created.append(_paired(conn, out, inn, confident=confident))
    return created


def _paired(conn: Connection, out, inn, *, confident: bool) -> int:  # type: ignore[no-untyped-def]
    """One transfer transaction from the two statement lines that are its halves."""
    return post(
        conn,
        NewTxn(
            date=dt.date.fromisoformat(out.date),
            description=out.description,
            payee=f"Transfer → {inn.account}",
            kind="transfer",
            import_file_id=out.import_file_id,
            status="confirmed" if confident else "pending",
            weight_commodity=out.commodity,
            note=f"paired with: {inn.description} ({inn.date})",
            postings=[
                NewPosting(
                    out.account,
                    from_minor(out.amount, out.precision),
                    out.commodity,
                    source_key=out.source_key,
                ),
                NewPosting(
                    inn.account,
                    from_minor(inn.amount, inn.precision),
                    inn.commodity,
                    source_key=inn.source_key,
                ),
            ],
        ),
    )
