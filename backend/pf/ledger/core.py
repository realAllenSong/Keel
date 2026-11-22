"""The only write path into the ledger.

`post()` turns a NewTxn into rows, balances it exactly in integer minor units and seals it. The
database triggers then refuse any later change that would unbalance it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import Connection, text

from pf.money import Commodities, to_minor

ACCOUNT_TYPES = ("Assets", "Liabilities", "Income", "Expenses", "Equity")
UNCATEGORIZED_EXPENSE = "Expenses:Uncategorized"
UNCATEGORIZED_INCOME = "Income:Uncategorized"
OPENING_BALANCES = "Equity:Opening-Balances"


class LedgerError(ValueError):
    pass


@dataclass
class NewPosting:
    account: str
    amount: Decimal | None  # None = "whatever balances the transaction" (at most one per txn)
    commodity: str = "USD"
    weight: Decimal | None = None  # value in the txn's weight commodity, for foreign postings
    source_key: str | None = None
    memo: str | None = None


@dataclass
class NewTxn:
    date: dt.date
    description: str
    postings: list[NewPosting]
    payee: str | None = None
    kind: str = "normal"
    status: str = "pending"
    import_file_id: int | None = None
    note: str | None = None
    weight_commodity: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def account_type(name: str) -> str:
    head = name.split(":", 1)[0]
    if head not in ACCOUNT_TYPES or ":" not in name:
        raise LedgerError(f"account name must look like 'Type:Sub[:Sub]', got {name!r}")
    return head


def ensure_account(
    conn: Connection,
    name: str,
    *,
    currency: str | None = None,
    institution: str | None = None,
    asset_class: str | None = None,
    flow_only: bool = False,
) -> int:
    row = conn.execute(text("SELECT id FROM accounts WHERE name = :n"), {"n": name}).first()
    if row:
        return int(row.id)
    acc_type = account_type(name)
    res = conn.execute(
        text(
            "INSERT INTO accounts(name, type, currency, institution, asset_class, flow_only) "
            "VALUES (:n, :t, :c, :i, :a, :f)"
        ),
        {
            "n": name,
            "t": acc_type,
            "c": currency,
            "i": institution,
            "a": asset_class,
            "f": int(flow_only),
        },
    )
    return int(res.lastrowid)  # type: ignore[arg-type]


def post(conn: Connection, txn: NewTxn) -> int:
    if len(txn.postings) < 2 and not any(p.amount is None for p in txn.postings):
        raise LedgerError("a transaction needs at least two postings")
    elided = [p for p in txn.postings if p.amount is None]
    if len(elided) > 1:
        raise LedgerError("at most one posting may have an elided amount")

    comms = Commodities(conn)
    wc = txn.weight_commodity or _infer_weight_commodity(txn.postings)
    wprec = comms.precision(wc, "currency")

    rows: list[dict[str, object]] = []
    for p in txn.postings:
        if p.amount is None:
            continue
        prec = comms.precision(p.commodity)
        amount = to_minor(p.amount, prec)
        if p.commodity == wc:
            weight, price = amount, None
        else:
            if p.weight is None:
                raise LedgerError(f"posting in {p.commodity} needs a weight in {wc}")
            weight = to_minor(p.weight, wprec, exact=False)
            price = str(p.weight / p.amount) if p.amount else None
        rows.append(_row(conn, p, amount, weight, price))

    residual = sum(int(r["weight"]) for r in rows)  # type: ignore[call-overload]
    if elided:
        p = elided[0]
        if p.commodity != wc:
            raise LedgerError("the elided posting must be in the weight commodity")
        rows.append(_row(conn, p, -residual, -residual, None))
    elif residual != 0:
        converted = [r for r in rows if r["price"] is not None]
        if converted and abs(residual) <= len(converted):
            converted[-1]["weight"] = int(converted[-1]["weight"]) - residual  # type: ignore[call-overload]
        else:
            raise LedgerError(
                f"unbalanced transaction on {txn.date} '{txn.description}': residual {residual}"
            )

    res = conn.execute(
        text(
            "INSERT INTO transactions(date, description, payee, kind, status, weight_commodity,"
            " import_file_id, note) VALUES (:d, :desc, :payee, :kind, :status, :wc, :f, :note)"
        ),
        {
            "d": txn.date.isoformat(),
            "desc": txn.description,
            "payee": txn.payee,
            "kind": txn.kind,
            "status": txn.status,
            "wc": wc,
            "f": txn.import_file_id,
            "note": txn.note,
        },
    )
    txn_id = int(res.lastrowid)  # type: ignore[arg-type]
    for r in rows:
        r["txn_id"] = txn_id
    conn.execute(
        text(
            "INSERT INTO postings(txn_id, account_id, amount, commodity, weight, price,"
            " source_key, memo) VALUES (:txn_id, :account_id, :amount, :commodity, :weight,"
            " :price, :source_key, :memo)"
        ),
        rows,
    )
    conn.execute(text("UPDATE transactions SET sealed = 1 WHERE id = :id"), {"id": txn_id})
    return txn_id


def delete_txn(conn: Connection, txn_id: int) -> None:
    conn.execute(text("UPDATE transactions SET sealed = 0 WHERE id = :id"), {"id": txn_id})
    conn.execute(text("DELETE FROM postings WHERE txn_id = :id"), {"id": txn_id})
    conn.execute(text("DELETE FROM transactions WHERE id = :id"), {"id": txn_id})


def recategorize(
    conn: Connection, posting_id: int, account: str, *, payee: str | None = None
) -> None:
    acc_id = ensure_account(conn, account)
    conn.execute(
        text("UPDATE postings SET account_id = :a WHERE id = :p"), {"a": acc_id, "p": posting_id}
    )
    if payee is not None:
        conn.execute(
            text(
                "UPDATE transactions SET payee = :payee"
                " WHERE id = (SELECT txn_id FROM postings WHERE id = :p)"
            ),
            {"payee": payee, "p": posting_id},
        )


def _infer_weight_commodity(postings: list[NewPosting]) -> str:
    without_weight = {p.commodity for p in postings if p.weight is None}
    if len(without_weight) == 1:
        return without_weight.pop()
    if not without_weight:
        raise LedgerError("cannot infer weight commodity: every posting carries a weight")
    raise LedgerError(f"mixed commodities {sorted(without_weight)} without weights")


def _row(conn: Connection, p: NewPosting, amount: int, weight: int, price: str | None):  # type: ignore[no-untyped-def]
    return {
        "account_id": ensure_account(conn, p.account),
        "amount": amount,
        "commodity": p.commodity,
        "weight": weight,
        "price": price,
        "source_key": p.source_key,
        "memo": p.memo,
    }
