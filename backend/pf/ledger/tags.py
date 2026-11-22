"""Tags: a second way to group transactions, across categories ("japan-trip": the flights, the
hotel, the dinners). Stored per statement line (account + source key) so a rebuild keeps them;
a hand-entered transaction keeps its tags under its own id (see migration 0017)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import Connection, text

from pf.reports.common import Fx, cents

MANUAL = "manual:"

# The transactions carrying a tag (bind :tag), as a subquery for `id IN (...)`.
TAGGED = """
SELECT p.txn_id FROM postings p JOIN accounts a ON a.id = p.account_id
  JOIN line_tags lt ON lt.account_name = a.name AND lt.source_key = p.source_key
 WHERE lt.tag = :tag
UNION
SELECT CAST(substr(source_key, 8) AS INTEGER) FROM line_tags
 WHERE tag = :tag AND account_name = '' AND source_key LIKE 'manual:%'
"""


def clean(tag: str) -> str:
    """One tag as stored: trimmed, inner spaces single, no commas or '#', at most 40 characters."""
    flat = re.sub(r"\s+", " ", tag.replace(",", " ").replace("，", " ")).strip()
    return flat.lstrip("#").strip()[:40]


def _keys(conn: Connection, txn_id: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        text(
            "SELECT a.name, p.source_key FROM postings p JOIN accounts a ON a.id = p.account_id"
            " WHERE p.txn_id = :t AND p.source_key IS NOT NULL ORDER BY p.id"
        ),
        {"t": txn_id},
    ).all()
    return [(r.name, r.source_key) for r in rows] or [("", f"{MANUAL}{txn_id}")]


def tags_for(conn: Connection, txn_ids: list[int]) -> dict[int, list[str]]:
    if not txn_ids:
        return {}
    ids = json.dumps(txn_ids)
    rows = conn.execute(
        text(
            """
            SELECT p.txn_id AS id, lt.tag FROM postings p JOIN accounts a ON a.id = p.account_id
              JOIN line_tags lt ON lt.account_name = a.name AND lt.source_key = p.source_key
             WHERE p.txn_id IN (SELECT value FROM json_each(:ids))
            UNION
            SELECT CAST(substr(source_key, 8) AS INTEGER), tag FROM line_tags
             WHERE account_name = '' AND source_key LIKE 'manual:%'
               AND CAST(substr(source_key, 8) AS INTEGER) IN (SELECT value FROM json_each(:ids))
            ORDER BY 2
            """
        ),
        {"ids": ids},
    ).all()
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r.id, []).append(r.tag)
    return out


def add(conn: Connection, txn_ids: list[int], tag: str) -> int:
    tag = clean(tag)
    if not tag:
        return 0
    for t in txn_ids:
        account, key = _keys(conn, t)[0]
        conn.execute(
            text(
                "INSERT OR IGNORE INTO line_tags(tag, account_name, source_key) VALUES (:g, :a, :k)"
            ),
            {"g": tag, "a": account, "k": key},
        )
    return len(txn_ids)


def remove(conn: Connection, txn_ids: list[int], tag: str | None = None) -> None:
    """Take `tag` (or every tag) off these transactions."""
    for t in txn_ids:
        for account, key in [*_keys(conn, t), ("", f"{MANUAL}{t}")]:
            conn.execute(
                text(
                    "DELETE FROM line_tags WHERE account_name = :a AND source_key = :k"
                    " AND (:g IS NULL OR tag = :g)"
                ),
                {"a": account, "k": key, "g": clean(tag) if tag else None},
            )


def set_tags(conn: Connection, txn_id: int, tags: list[str]) -> list[str]:
    remove(conn, [txn_id])
    wanted = sorted({c for t in tags if (c := clean(t))})
    for tag in wanted:
        add(conn, [txn_id], tag)
    return wanted


def moved(conn: Connection, old_id: int, new_id: int) -> None:
    """A hand-entered transaction was re-posted under a new id: its tags follow."""
    conn.execute(
        text(
            "UPDATE OR REPLACE line_tags SET source_key = :n WHERE account_name = ''"
            " AND source_key = :o"
        ),
        {"o": f"{MANUAL}{old_id}", "n": f"{MANUAL}{new_id}"},
    )


@dataclass
class TagTotal:
    tag: str
    count: int
    spent: str  # in the report currency
    income: str
    first: str
    last: str


def summary(conn: Connection, currency: str = "USD") -> list[TagTotal]:
    """Every tag with what was spent and received under it, newest tag first."""
    fx = Fx(conn, currency)
    out = []
    for (tag,) in conn.execute(text("SELECT DISTINCT tag FROM line_tags ORDER BY tag")).all():
        rows = conn.execute(
            text(
                f"""
                SELECT vp.txn_id, vp.date, vp.account_type, vp.amount, vp.commodity
                FROM v_postings vp WHERE vp.txn_id IN ({TAGGED})
                  AND vp.account_type IN ('Expenses', 'Income')
                """
            ),
            {"tag": tag},
        ).all()
        ids = conn.execute(
            text(f"SELECT id, date FROM transactions WHERE id IN ({TAGGED})"), {"tag": tag}
        ).all()
        if not ids:
            continue  # the lines it was on are gone
        spent = sum(
            fx.to_base(r.amount, r.commodity, r.date) for r in rows if r.account_type == "Expenses"
        )
        income = -sum(
            fx.to_base(r.amount, r.commodity, r.date) for r in rows if r.account_type == "Income"
        )
        days = [r.date for r in ids]
        out.append(TagTotal(tag, len(ids), cents(spent), cents(income), min(days), max(days)))
    out.sort(key=lambda t: t.last, reverse=True)
    return out


# ---------------------------------------------------------------- rules
@dataclass
class TagRule:
    id: int
    pattern: str
    tag: str


def rules(conn: Connection) -> list[TagRule]:
    return [
        TagRule(r.id, r.pattern, r.tag)
        for r in conn.execute(text("SELECT id, pattern, tag FROM tag_rules ORDER BY id"))
    ]


def _matching(conn: Connection, pattern: str, txn_ids: list[int] | None) -> list[int]:
    """Transactions (all, or among `txn_ids`) whose description or payee the pattern matches,
    by the same test a category rule uses."""
    from pf.ledger.categorize import Rule

    rule = Rule(pattern=pattern, target="")
    rows = conn.execute(
        text(
            "SELECT id, description, payee FROM transactions WHERE kind IN ('normal', 'manual')"
            + (" AND id IN (SELECT value FROM json_each(:ids))" if txn_ids is not None else "")
        ),
        {"ids": json.dumps(txn_ids)} if txn_ids is not None else {},
    ).all()
    return [
        r.id
        for r in rows
        if rule.matches(r.description, None, "", 0)
        or (r.payee and rule.matches(r.payee, None, "", 0))
    ]


def add_rule(conn: Connection, pattern: str, tag: str) -> int:
    """From now on, and for every line already in the ledger: `pattern` -> `tag`.
    Returns how many existing lines it tagged."""
    pattern, tag = " ".join(pattern.lower().split()), clean(tag)
    if len(pattern) < 2 or not tag:
        raise ValueError("a tag rule needs a merchant word of two characters or more, and a tag")
    conn.execute(
        text("INSERT OR IGNORE INTO tag_rules(pattern, tag) VALUES (:p, :g)"),
        {"p": pattern, "g": tag},
    )
    return add(conn, _matching(conn, pattern, None), tag)


def delete_rule(conn: Connection, rule_id: int) -> None:
    """Stop tagging new lines; lines already tagged keep their tag."""
    conn.execute(text("DELETE FROM tag_rules WHERE id = :i"), {"i": rule_id})


def apply_rules(conn: Connection, txn_ids: list[int]) -> None:
    """Tag newly imported lines by the rules (pf/ledger/pipeline.py)."""
    if not txn_ids:
        return
    for rule in rules(conn):
        add(conn, _matching(conn, rule.pattern, txn_ids), rule.tag)
