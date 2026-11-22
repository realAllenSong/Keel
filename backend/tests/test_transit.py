"""Card payments seen from the bank's side first, and balances still waiting for their lines."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import Connection, text

from pf.ledger.importing import import_file
from pf.ledger.reconcile import check, reconcile, waiting_for_sync

_n = 0


def _sync(
    tmp: Path, account: str, institution: str, kind: str, day: str, balance: str | None, lines: list
) -> Path:
    """One connector statement: (id, date, amount, description) lines and the balance that day."""
    global _n
    _n += 1
    doc = {
        "keel_connector": 1,
        "fetched_at": f"{day}T12:00:00+00:00",
        "source": "plaid",
        "account_id": account,
        "institution": institution,
        "type": kind,
        "currency": "USD",
        "name": "Card" if kind == "Liabilities" else "Checking",
        "mask": account[-4:],
        "asset_class": None if kind == "Liabilities" else "cash",
        "balances": [{"date": day, "amount": balance}] if balance is not None else [],
        "valuations": [],
        "transactions": [
            {"id": i, "date": d, "amount": a, "description": desc} for i, d, a, desc in lines
        ],
        "holdings": [],
    }
    p = tmp / f"s{_n}.json"
    p.write_text(json.dumps(doc))
    return p


def _balance(conn: Connection, like: str) -> int:
    return conn.execute(
        text("SELECT COALESCE(SUM(amount), 0) FROM v_postings WHERE account LIKE :a"), {"a": like}
    ).scalar_one()


def _spent(conn: Connection) -> int:
    return conn.execute(
        text("SELECT COALESCE(SUM(amount), 0) FROM v_postings WHERE account_type = 'Expenses'")
    ).scalar_one()


def test_card_payment_is_a_transfer_before_the_card_lists_it(
    conn: Connection, tmp_path: Path
) -> None:
    bank, amex = "bank-0101", "amex-0007"
    import_file(conn, _sync(tmp_path, amex, "American Express", "Liabilities", "2026-11-16", "-500.00",
                            [("c1", "2026-11-01", "-500.00", "DESIGNER BOUTIQUE")]))  # fmt: skip
    import_file(conn, _sync(tmp_path, bank, "Bank of America", "Assets", "2026-11-16", "9000.00",
                            [("b0", "2026-11-03", "9000.00", "PAYROLL")]))  # fmt: skip
    spent = _spent(conn)
    assert spent == 50000

    # 11/18: the bank shows the payment; the card still reports the old balance.
    import_file(conn, _sync(tmp_path, bank, "Bank of America", "Assets", "2026-11-18", "8500.00",
                            [("b1", "2026-11-18", "-500.00", "AMERICAN EXPRESS DES:ACH PMT ID:M0290")]))  # fmt: skip
    import_file(
        conn, _sync(tmp_path, amex, "American Express", "Liabilities", "2026-11-19", "-500.00", [])
    )
    paid = conn.execute(
        text(
            "SELECT kind, status, category_source FROM transactions WHERE description LIKE 'AMERICAN%'"
        )
    ).one()
    assert tuple(paid) == ("transfer", "confirmed", "transit")
    assert _spent(conn) == spent  # the payment is not spending
    assert _balance(conn, "Liabilities:American Express%") == 0  # the debt is gone from net worth
    assert _balance(conn, "Assets:%") == 850000
    assert check(conn, today=dt.date(2026, 11, 19)) == []  # the card's stale balance is explained

    # 11/20: the card lists the payment. One paired transfer, nothing double.
    import_file(conn, _sync(tmp_path, amex, "American Express", "Liabilities", "2026-11-20", "0.00",
                            [("c2", "2026-11-19", "500.00", "ONLINE PAYMENT - THANK YOU")]))  # fmt: skip
    transfers = conn.execute(
        text("SELECT category_source, (SELECT COUNT(*) FROM postings p WHERE p.txn_id = t.id"
             " AND p.source_key IS NOT NULL) AS keyed FROM transactions t WHERE kind = 'transfer'")
    ).all()  # fmt: skip
    assert [(t.category_source, t.keyed) for t in transfers] == [(None, 2)]
    assert _balance(conn, "Liabilities:American Express%") == 0 and _spent(conn) == spent
    assert check(conn, today=dt.date(2026, 11, 20)) == []


def test_issuer_with_two_cards_needs_the_exact_balance(conn: Connection, tmp_path: Path) -> None:
    for acct, owed in (("chase-0003", "-2500.00"), ("chase-0002", "-150.00")):
        import_file(conn, _sync(tmp_path, acct, "Chase", "Liabilities", "2026-11-10", owed,
                                [(acct, "2026-11-02", owed, "SOME STORE")]))  # fmt: skip
    import_file(conn, _sync(tmp_path, "bank-0101", "Bank of America", "Assets", "2026-11-12", None, [
        ("p1", "2026-11-12", "-150.00", "CHASE CREDIT CRD DES:AUTOPAY"),
        ("p2", "2026-11-12", "-250.00", "CHASE CREDIT CRD DES:EPAY"),  # matches neither balance
        ("p3", "2026-11-12", "-40.00", "CHASE PHARMACY 123"),  # not a payment at all
    ]))  # fmt: skip
    got = {
        r.description: (r.kind, r.category_source, r.account)
        for r in conn.execute(
            text(
                "SELECT t.description, t.kind, t.category_source, a.name AS account FROM transactions t"
                " JOIN postings p ON p.txn_id = t.id AND p.source_key IS NULL"
                " JOIN accounts a ON a.id = p.account_id WHERE t.description LIKE 'CHASE%'"
            )
        )
    }
    assert got["CHASE CREDIT CRD DES:AUTOPAY"] == (
        "transfer",
        "transit",
        "Liabilities:Chase:Card-0002",
    )
    assert got["CHASE CREDIT CRD DES:EPAY"][:2] == ("normal", None)  # left for pairing or review
    assert got["CHASE PHARMACY 123"][0] == "normal"


def test_a_fresh_gap_waits_for_sync_and_an_old_one_is_reported(
    conn: Connection, tmp_path: Path
) -> None:
    card = "chase-0004"
    import_file(
        conn, _sync(tmp_path, card, "Chase", "Liabilities", "2026-11-01", "0.00", [])
    )  # paid off
    import_file(conn, _sync(tmp_path, card, "Chase", "Liabilities", "2026-11-19", "-1.08",
                            [("a", "2026-11-15", "-1.08", "CURSOR USAGE")]))  # fmt: skip
    # The balance moved; the charge behind it has not been listed yet.
    import_file(conn, _sync(tmp_path, card, "Chase", "Liabilities", "2026-11-20", "-11.97", []))
    day = dt.date(2026, 11, 20)
    gaps = check(conn, today=day)
    assert [(g.date, g.diff, g.waiting) for g in gaps] == [("2026-11-20", "10.89", True)]
    # Six days on, with every sync still disagreeing: this is a hole, and it is said so.
    for d in ("2026-11-22", "2026-11-24", "2026-11-26"):
        import_file(conn, _sync(tmp_path, card, "Chase", "Liabilities", d, "-11.97", []))
    assert all(not g.waiting for g in check(conn, today=dt.date(2026, 11, 26)))
    # The line arrives: every snapshot it explains is matched.
    import_file(conn, _sync(tmp_path, card, "Chase", "Liabilities", "2026-11-27", "-11.97",
                            [("b", "2026-11-19", "-10.89", "COFFEE")]))  # fmt: skip
    assert check(conn, today=dt.date(2026, 11, 27)) == []
    assert reconcile(conn) == [] and waiting_for_sync(conn) == []
