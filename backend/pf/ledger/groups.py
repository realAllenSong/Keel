"""Uncategorised lines grouped by who the money went to or came from, so one decision covers a
merchant or a person: 'ZELLE PAYMENT FROM WEICI ZHAO' ×17 is one question, not seventeen.

The group key is also the rule pattern (a 'contains' match on the description), so a decision
can be remembered for future lines of the same merchant or person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import Connection, text

from pf.ledger.categorize import merchant_key, set_category
from pf.ledger.core import UNCATEGORIZED_EXPENSE, UNCATEGORIZED_INCOME
from pf.ledger.dedup import normalize

# Person-to-person apps: the counterparty's name is the merchant.
_P2P = re.compile(r"^((?:zelle|venmo|cash app|paypal)(?: payment| transfer)? (?:from|to)) (.*)$")
_STOP = {"conf", "on", "ref", "id", "des", "for", "memo"}


def _prefix(s: str, start: int = 0, limit: int = 3) -> str:
    """The leading plain words of s[start:], as they appear in s (so the key is a substring of
    the description and works as a rule pattern): stop at a code, a number or a filler word."""
    end, kept = start, 0
    for m in re.finditer(r"\S+", s[start:]):
        tok = m.group().strip(",.;:")
        if not tok or any(ch.isdigit() for ch in tok) or "#" in tok or tok in _STOP:
            break
        end, kept = start + m.end(), kept + 1
        if kept == limit:
            break
    return s[:end].rstrip(",.;: ")


_PROCESSOR = re.compile(r"^([a-z]{1,4}) ?\*")  # "IC* INSTACART", "TST* ZHU", "CL *Chase Travel"


def group_key(description: str) -> str:
    full = normalize(description)
    if m := _PROCESSOR.match(full):
        # A payment processor's prefix is not a merchant: keep it with its star ("ic*"), which
        # only that processor's lines contain. The bare letters would match half the ledger.
        return m.group(0)
    s = full.split("*", 1)[0]
    if m := _P2P.match(s):
        named = _prefix(s, m.start(2))
        if len(named) > m.end(1):
            return named
    return _prefix(s) or merchant_key(description)


@dataclass
class Group:
    key: str
    direction: str  # out | in
    count: int = 0
    total: int = 0  # minor units, ledger sign of the statement side
    accounts: set[str] = field(default_factory=set)
    samples: list[str] = field(default_factory=list)
    txn_ids: list[int] = field(default_factory=list)
    first: str = ""
    last: str = ""


def uncategorized_groups(conn: Connection) -> list[Group]:
    rows = conn.execute(
        text(
            """SELECT t.id, t.date, t.description, sa.name AS account, s.amount
               FROM transactions t
               JOIN postings s ON s.txn_id = t.id AND s.source_key IS NOT NULL
               JOIN accounts sa ON sa.id = s.account_id
               JOIN postings q ON q.txn_id = t.id AND q.id != s.id
               JOIN accounts c ON c.id = q.account_id
               WHERE t.status = 'pending' AND t.kind = 'normal' AND c.name IN (:ue, :ui)
               ORDER BY t.date"""
        ),
        {"ue": UNCATEGORIZED_EXPENSE, "ui": UNCATEGORIZED_INCOME},
    ).all()
    groups: dict[tuple[str, str], Group] = {}
    for r in rows:
        direction = "out" if r.amount < 0 else "in"
        g = groups.setdefault(
            (group_key(r.description), direction), Group(group_key(r.description), direction)
        )
        g.count += 1
        g.total += int(r.amount)
        g.accounts.add(r.account)
        g.txn_ids.append(int(r.id))
        g.first = g.first or r.date
        g.last = r.date
        if len(g.samples) < 3 and r.description not in g.samples:
            g.samples.append(r.description)
    return sorted(groups.values(), key=lambda g: (-g.count, -abs(g.total)))


def categorize_group(conn: Connection, txn_ids: list[int], category: str, *, remember: bool) -> int:
    """File every line of a group under `category`; with `remember`, future lines too."""
    if not txn_ids:
        return 0
    desc = conn.execute(
        text("SELECT description FROM transactions WHERE id = :i"), {"i": txn_ids[0]}
    ).scalar_one()
    set_category(
        conn,
        txn_ids[0],
        category,
        remember="rule" if remember else "override",
        rule_pattern=group_key(desc),
    )
    still = (
        conn.execute(
            text(
                "SELECT id FROM transactions WHERE status = 'pending'"
                " AND id IN (SELECT value FROM json_each(:ids))"
            ),
            {"ids": str(txn_ids[1:])},
        )
        .scalars()
        .all()
    )
    for tid in still:
        set_category(conn, int(tid), category, remember="override")
    return len(txn_ids)
