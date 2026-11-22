import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from pf.ledger.core import LedgerError, NewPosting, NewTxn, delete_txn, post, recategorize

DAY = dt.date(2026, 10, 1)


def _txn(*postings: NewPosting, **kw) -> NewTxn:  # type: ignore[no-untyped-def]
    return NewTxn(DAY, "t", list(postings), **kw)


def test_balanced_txn_seals(conn: Connection) -> None:
    tid = post(
        conn, _txn(NewPosting("Expenses:Food", D("10.01")), NewPosting("Assets:Cash:Wallet", None))
    )
    row = conn.execute(text("SELECT sealed FROM transactions WHERE id=:i"), {"i": tid}).one()
    assert row.sealed == 1
    assert conn.execute(text("SELECT COUNT(*) FROM v_unbalanced")).scalar() == 0


def test_unbalanced_rejected_by_python(conn: Connection) -> None:
    with pytest.raises(LedgerError):
        post(conn, _txn(NewPosting("Expenses:Food", D("1")), NewPosting("Assets:Cash:W", D("-2"))))


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE postings SET amount = amount + 1",
        "UPDATE postings SET weight = 0",
        "INSERT INTO postings(txn_id, account_id, amount, commodity, weight) VALUES (1, 1, 1, 'USD', 1)",
        "DELETE FROM postings",
    ],
)
def test_db_triggers_protect_sealed(conn: Connection, sql: str) -> None:
    post(conn, _txn(NewPosting("Expenses:Food", D("5")), NewPosting("Assets:Cash:W", None)))
    with pytest.raises(IntegrityError):
        conn.execute(text(sql))


def test_db_refuses_sealing_unbalanced(conn: Connection) -> None:
    conn.execute(text("INSERT INTO accounts(name, type) VALUES ('Assets:A:B', 'Assets')"))
    conn.execute(
        text(
            "INSERT INTO transactions(id, date, weight_commodity) VALUES (99, '2026-01-01', 'USD')"
        )
    )
    conn.execute(
        text(
            "INSERT INTO postings(txn_id, account_id, amount, commodity, weight) VALUES (99,1,5,'USD',5)"
        )
    )
    conn.execute(
        text(
            "INSERT INTO postings(txn_id, account_id, amount, commodity, weight) VALUES (99,1,-4,'USD',-4)"
        )
    )
    with pytest.raises(IntegrityError):
        conn.execute(text("UPDATE transactions SET sealed = 1 WHERE id = 99"))


def test_fx_rounding_residual_absorbed(conn: Connection) -> None:
    tid = post(
        conn,
        _txn(
            NewPosting("Assets:Bank:USD", D("-100.00")),
            NewPosting("Assets:Bank:CNY", D("333.33"), "CNY", weight=D("33.333")),
            NewPosting("Assets:Bank:CNY2", D("333.33"), "CNY", weight=D("33.333")),
            NewPosting("Assets:Bank:CNY3", D("333.34"), "CNY", weight=D("33.334")),
        ),
    )
    total = conn.execute(
        text("SELECT SUM(weight) FROM postings WHERE txn_id=:i"), {"i": tid}
    ).scalar()
    assert total == 0


def test_recategorize_allowed_and_delete_works(conn: Connection) -> None:
    tid = post(
        conn, _txn(NewPosting("Expenses:Uncategorized", D("5")), NewPosting("Assets:Cash:W", None))
    )
    pid = conn.execute(
        text("SELECT id FROM postings WHERE txn_id=:i AND amount>0"), {"i": tid}
    ).scalar_one()
    recategorize(conn, pid, "Expenses:Food:Coffee", payee="Blue Bottle")
    acc = conn.execute(text("SELECT account FROM v_postings WHERE id=:p"), {"p": pid}).scalar_one()
    assert acc == "Expenses:Food:Coffee"
    delete_txn(conn, tid)
    assert conn.execute(text("SELECT COUNT(*) FROM postings")).scalar() == 0


def test_bad_account_names(conn: Connection) -> None:
    with pytest.raises(LedgerError):
        post(conn, _txn(NewPosting("Food", D("1")), NewPosting("Assets:Cash:W", None)))
