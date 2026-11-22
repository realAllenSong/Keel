"""Changing the ledger by hand: cash you spent that no statement shows, and fixes to imported
lines (date, amount, description, payee, a split across categories, deletion).

A manual transaction has no statement line behind it, so `rebuild` keeps it as it is. A fix to an
imported line is stored in `line_edits` under the line's account and source key, and applied
every time that statement is imported (see `apply_to_row` and `post_line`), so a rebuild gives
the same ledger. The fix is also applied right away: the line is re-posted and re-categorised.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection, text

from pf.importers.base import RawTxn
from pf.ledger.core import (
    UNCATEGORIZED_EXPENSE,
    UNCATEGORIZED_INCOME,
    LedgerError,
    NewPosting,
    NewTxn,
    delete_txn,
    post,
    recategorize,
)
from pf.money import from_minor


@dataclass
class LineEdit:
    deleted: bool = False
    date: dt.date | None = None
    amount: Decimal | None = None
    description: str | None = None
    payee: str | None = None
    note: str | None = None
    splits: list[tuple[str, Decimal]] | None = None


def load_edits(conn: Connection, account: str) -> dict[str, LineEdit]:
    out: dict[str, LineEdit] = {}
    for r in conn.execute(text("SELECT * FROM line_edits WHERE account_name = :a"), {"a": account}):
        out[r.source_key] = LineEdit(
            deleted=bool(r.deleted),
            date=dt.date.fromisoformat(r.date) if r.date else None,
            amount=Decimal(r.amount) if r.amount is not None else None,
            description=r.description,
            payee=r.payee,
            note=r.note,
            splits=[(c, Decimal(a)) for c, a in json.loads(r.splits)] if r.splits else None,
        )
    return out


def apply_to_row(row: RawTxn, edit: LineEdit) -> RawTxn:
    """The statement line as you corrected it."""
    return RawTxn(
        date=edit.date or row.date,
        amount=edit.amount if edit.amount is not None else row.amount,
        description=edit.description if edit.description is not None else row.description,
        payee=edit.payee if edit.payee is not None else row.payee,
        ref=row.ref,
        category_hint=row.category_hint,
        balance_after=row.balance_after,
        memo=edit.note if edit.note is not None else row.memo,
        category=row.category,
    )


def post_line(
    conn: Connection,
    account: str,
    currency: str,
    row: RawTxn,
    key: str,
    file_id: int | None,
    edit: LineEdit | None = None,
) -> int:
    """Post one statement line: one category leg (uncategorised unless the statement states it),
    or one leg per category when you split it."""
    if edit is not None and edit.splits:
        sign = Decimal(1) if row.amount < 0 else Decimal(-1)  # category legs mirror the line
        total = sum((a for _, a in edit.splits), Decimal(0))
        if total != abs(row.amount):
            raise LedgerError(f"split parts add up to {total}, the line is {abs(row.amount)}")
        legs = [NewPosting(c, sign * a, currency) for c, a in edit.splits]
        return post(
            conn,
            NewTxn(
                date=row.date,
                description=row.description,
                payee=row.payee,
                note=row.memo,
                import_file_id=file_id,
                weight_commodity=currency,
                status="confirmed",
                postings=[NewPosting(account, row.amount, currency, source_key=key), *legs],
            ),
        )
    counter = row.category or (UNCATEGORIZED_EXPENSE if row.amount < 0 else UNCATEGORIZED_INCOME)
    return post(
        conn,
        NewTxn(
            date=row.date,
            description=row.description,
            payee=row.payee,
            note=row.memo,
            import_file_id=file_id,
            weight_commodity=currency,
            status="confirmed" if row.category else "pending",
            postings=[
                NewPosting(account, row.amount, currency, source_key=key),
                NewPosting(counter, -row.amount, currency, memo=row.category_hint),
            ],
        ),
    )


# ---------------------------------------------------------------- manual transactions
def add_manual(
    conn: Connection,
    *,
    date: dt.date,
    account: str,
    amount: Decimal,
    category: str,
    description: str,
    payee: str | None = None,
    commodity: str | None = None,
    note: str | None = None,
) -> int:
    """Money that moved without a statement line: `amount` in the ledger sign of `account`
    (negative = paid out of it), booked against `category`."""
    from pf.ledger.core import ensure_account

    if not account.startswith(("Assets:", "Liabilities:")):
        raise LedgerError("pay from (or into) one of your accounts")
    cur = (
        commodity
        or conn.execute(
            text("SELECT currency FROM accounts WHERE name = :n"), {"n": account}
        ).scalar()
        or "USD"
    )
    ensure_account(
        conn, account, currency=cur, asset_class="cash" if account.startswith("Assets") else None
    )
    own = category.startswith(("Assets:", "Liabilities:"))
    tid = post(
        conn,
        NewTxn(
            date=date,
            description=description,
            payee=payee,
            note=note,
            kind="transfer" if own else "manual",
            status="confirmed",
            weight_commodity=cur,
            postings=[NewPosting(account, amount, cur), NewPosting(category, -amount, cur)],
        ),
    )
    conn.execute(text("UPDATE transactions SET category_source = 'user' WHERE id = :t"), {"t": tid})
    return tid


# ---------------------------------------------------------------- fixes to any transaction
@dataclass
class _Txn:
    id: int
    kind: str
    import_file_id: int | None
    date: str
    description: str
    payee: str | None
    note: str | None
    currency: str
    account: str | None  # the statement leg's account (imported lines)
    source_key: str | None
    amount: Decimal | None  # the statement leg (imported) or first own-account leg (manual)
    category_hint: str | None


def _load(conn: Connection, txn_id: int) -> _Txn:
    t = conn.execute(
        text(
            "SELECT id, kind, import_file_id, date, description, payee, note, weight_commodity"
            " FROM transactions WHERE id = :t AND sealed = 1"
        ),
        {"t": txn_id},
    ).first()
    if t is None:
        raise LedgerError(f"no transaction {txn_id}")
    legs = conn.execute(
        text(
            "SELECT a.name, a.type, p.amount, c.precision, p.source_key, p.memo FROM postings p"
            " JOIN accounts a ON a.id = p.account_id JOIN commodities c ON c.code = p.commodity"
            " WHERE p.txn_id = :t ORDER BY (p.source_key IS NULL), p.id"
        ),
        {"t": txn_id},
    ).all()
    stated = [x for x in legs if x.source_key]
    if len(stated) > 1:
        raise LedgerError("this is a transfer between two statements: undo the pairing first")
    main = (
        stated[0]
        if stated
        else next((x for x in legs if x.type in ("Assets", "Liabilities")), None)
    )
    hint = next((x.memo for x in legs if not x.source_key and x.memo), None)
    return _Txn(
        id=t.id,
        kind=t.kind,
        import_file_id=t.import_file_id,
        date=t.date,
        description=t.description,
        payee=t.payee,
        note=t.note,
        currency=t.weight_commodity,
        account=main.name if main else None,
        source_key=main.source_key if main else None,
        amount=from_minor(main.amount, main.precision) if main else None,
        category_hint=hint,
    )


def _save_edit(conn: Connection, t: _Txn, edit: LineEdit) -> None:
    conn.execute(
        text(
            "INSERT INTO line_edits(account_name, source_key, deleted, date, amount, description,"
            " payee, note, splits) VALUES (:a, :k, :del, :d, :amt, :desc, :p, :n, :s)"
            " ON CONFLICT(account_name, source_key) DO UPDATE SET deleted = excluded.deleted,"
            " date = excluded.date, amount = excluded.amount, description = excluded.description,"
            " payee = excluded.payee, note = excluded.note, splits = excluded.splits"
        ),
        {
            "a": t.account,
            "k": t.source_key,
            "del": int(edit.deleted),
            "d": edit.date.isoformat() if edit.date else None,
            "amt": str(edit.amount) if edit.amount is not None else None,
            "desc": edit.description,
            "p": edit.payee,
            "n": edit.note,
            "s": json.dumps([[c, str(a)] for c, a in edit.splits]) if edit.splits else None,
        },
    )


def _merged(conn: Connection, t: _Txn) -> LineEdit:
    return load_edits(conn, t.account or "").get(t.source_key or "", LineEdit())


def _repost(conn: Connection, t: _Txn, edit: LineEdit) -> int:
    """Post the imported line again with your fixes and let the pipeline categorise it."""
    from pf.ledger.importing import refresh_opening_balance
    from pf.ledger.pipeline import after_import

    assert t.account and t.source_key and t.amount is not None
    # A category you picked for this line (without a saved rule) carries over to the new post.
    kept = conn.execute(
        text(
            "SELECT a.name FROM transactions x JOIN postings q ON q.txn_id = x.id"
            " AND q.source_key IS NULL JOIN accounts a ON a.id = q.account_id"
            " WHERE x.id = :t AND x.status = 'confirmed' AND x.kind = 'normal'"
            " AND (SELECT COUNT(*) FROM postings WHERE txn_id = x.id) = 2"
        ),
        {"t": t.id},
    ).scalar()
    original = RawTxn(
        date=dt.date.fromisoformat(t.date),
        amount=t.amount,
        description=t.description,
        payee=t.payee,
        memo=t.note,
        category_hint=t.category_hint,
    )
    delete_txn(conn, t.id)
    new_id = post_line(
        conn,
        t.account,
        t.currency,
        apply_to_row(original, edit),
        t.source_key,
        t.import_file_id,
        edit,
    )
    after_import(conn, [new_id])
    if kept and not edit.splits:
        cat = conn.execute(
            text(
                "SELECT q.id FROM transactions x JOIN postings q ON q.txn_id = x.id"
                " AND q.source_key IS NULL WHERE x.id = :t AND x.status = 'pending'"
                " AND x.kind = 'normal'"
            ),
            {"t": new_id},
        ).scalar()
        if cat is not None:
            recategorize(conn, cat, kept)
            conn.execute(
                text(
                    "UPDATE transactions SET status = 'confirmed', category_source = 'user' WHERE id = :t"
                ),
                {"t": new_id},
            )
    refresh_opening_balance(conn, t.account)
    return new_id


def edit_txn(
    conn: Connection,
    txn_id: int,
    *,
    date: dt.date | None = None,
    amount: Decimal | None = None,
    description: str | None = None,
    payee: str | None = None,
    note: str | None = None,
) -> int:
    """Change what a transaction says. Returns its (possibly new) id."""
    t = _load(conn, txn_id)
    if t.source_key is None:  # manual: just rewrite it
        return _rewrite_manual(
            conn, t, date=date, amount=amount, description=description, payee=payee, note=note
        )
    edit = _merged(conn, t)
    edit.date = date or edit.date
    edit.amount = amount if amount is not None else edit.amount
    edit.description = description if description is not None else edit.description
    edit.payee = payee if payee is not None else edit.payee
    edit.note = note if note is not None else edit.note
    if edit.splits and edit.amount is not None:
        edit.splits = None  # the old split no longer adds up to the new amount
    _save_edit(conn, t, edit)
    # the stored edit carries the full correction; post from the line as it now stands
    return _repost(
        conn,
        t,
        LineEdit(
            splits=edit.splits,
            date=date,
            amount=amount,
            description=description,
            payee=payee,
            note=note,
        ),
    )


def _rewrite_manual(conn: Connection, t: _Txn, **change: object) -> int:
    legs = conn.execute(
        text(
            "SELECT a.name, p.amount, c.precision FROM postings p JOIN accounts a"
            " ON a.id = p.account_id JOIN commodities c ON c.code = p.commodity"
            " WHERE p.txn_id = :t ORDER BY p.id"
        ),
        {"t": t.id},
    ).all()
    if len(legs) != 2 or t.amount is None or t.account is None:
        raise LedgerError("only simple manual entries can be edited")
    category = next(x.name for x in legs if x.name != t.account)
    delete_txn(conn, t.id)
    new_id = add_manual(
        conn,
        date=change["date"] or dt.date.fromisoformat(t.date),  # type: ignore[arg-type]
        account=t.account,
        amount=change["amount"] if change["amount"] is not None else t.amount,  # type: ignore[arg-type]
        category=category,
        description=change["description"] if change["description"] is not None else t.description,  # type: ignore[arg-type]
        payee=change["payee"] if change["payee"] is not None else t.payee,  # type: ignore[arg-type]
        commodity=t.currency,
        note=change["note"] if change["note"] is not None else t.note,  # type: ignore[arg-type]
    )
    from pf.ledger.tags import moved

    moved(conn, t.id, new_id)  # its tags are stored under its id
    return new_id


def split_txn(conn: Connection, txn_id: int, parts: list[tuple[str, Decimal]]) -> int:
    """Spread one line over several categories; the parts must add up to the line. An empty list
    puts it back to a single category (to be categorised again)."""
    t = _load(conn, txn_id)
    if t.source_key is None:
        raise LedgerError("split works on imported lines; enter manual ones as separate entries")
    if any(a <= 0 for _, a in parts):
        raise LedgerError("each part must be more than zero")
    total = sum((a for _, a in parts), Decimal(0))
    if parts and t.amount is not None and total != abs(t.amount):
        raise LedgerError(f"split parts add up to {total}, the line is {abs(t.amount)}")
    edit = _merged(conn, t)
    edit.splits = parts or None
    _save_edit(conn, t, edit)
    return _repost(conn, t, LineEdit(splits=edit.splits))


def delete_txn_by_user(conn: Connection, txn_id: int) -> None:
    """Remove a transaction. An imported line stays deleted when its statement is re-imported."""
    t = _load(conn, txn_id)
    if t.source_key is not None:
        edit = _merged(conn, t)
        edit.deleted = True
        _save_edit(conn, t, edit)
    delete_txn(conn, txn_id)
    if t.account:
        from pf.ledger.importing import refresh_opening_balance

        if t.source_key is not None:
            refresh_opening_balance(conn, t.account)
