"""Changing the ledger by hand: fixes to imported lines and manual entries survive a rebuild."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from pf.ledger.core import LedgerError
from pf.ledger.edits import add_manual, delete_txn_by_user, edit_txn, split_txn
from pf.ledger.importing import import_file
from pf.ledger.rebuild import rebuild
from tests.conftest import DEMO


@pytest.fixture
def demo(conn: Connection) -> Connection:
    for f in sorted(DEMO.glob("*")):
        import_file(conn, f)
    return conn


def _one(conn: Connection, like: str) -> int:
    return conn.execute(
        text("SELECT id FROM transactions WHERE description LIKE :d ORDER BY date, id LIMIT 1"),
        {"d": like},
    ).scalar_one()


def _legs(conn: Connection, txn_id: int) -> list[tuple[str, int]]:
    return [
        (r.name, r.amount)
        for r in conn.execute(
            text(
                "SELECT a.name, p.amount FROM postings p JOIN accounts a ON a.id = p.account_id"
                " WHERE p.txn_id = :t ORDER BY p.id"
            ),
            {"t": txn_id},
        )
    ]


def _by_key(conn: Connection, key: str) -> int | None:
    return conn.execute(
        text("SELECT txn_id FROM postings WHERE source_key = :k"), {"k": key}
    ).scalar()


def _key(conn: Connection, txn_id: int) -> str:
    return conn.execute(
        text("SELECT source_key FROM postings WHERE txn_id = :t AND source_key IS NOT NULL"),
        {"t": txn_id},
    ).scalar_one()


def test_edit_amount_date_and_payee_survive_rebuild(demo: Connection) -> None:
    tid = _one(demo, "WHOLEFDS%")
    key = _key(demo, tid)
    new = edit_txn(
        demo, tid, amount=Decimal("-99.99"), date=dt.date(2025, 1, 2), payee="Whole Foods"
    )
    row = demo.execute(text("SELECT date, payee FROM transactions WHERE id = :t"), {"t": new}).one()
    assert (row.date, row.payee) == ("2025-01-02", "Whole Foods")
    assert _legs(demo, new)[0][1] == -9999
    rebuild(demo)
    again = _by_key(demo, key)
    assert again is not None and _legs(demo, again)[0][1] == -9999
    assert (
        demo.execute(text("SELECT date FROM transactions WHERE id = :t"), {"t": again}).scalar()
        == "2025-01-02"
    )


def test_split_spreads_a_line_and_survives_rebuild(demo: Connection) -> None:
    tid = _one(demo, "AMAZON.COM%")
    key = _key(demo, tid)
    line = _legs(demo, tid)[0][1]  # e.g. -4599
    total = Decimal(-line) / 100
    half = (total / 2).quantize(Decimal("0.01"))
    new = split_txn(
        demo, tid, [("Expenses:Shopping:General", half), ("Expenses:Gifts", total - half)]
    )
    legs = _legs(demo, new)
    assert len(legs) == 3 and sum(a for _, a in legs) == 0
    assert (
        demo.execute(text("SELECT status FROM transactions WHERE id = :t"), {"t": new}).scalar()
        == "confirmed"
    )
    with pytest.raises(LedgerError):
        split_txn(demo, new, [("Expenses:Gifts", total + 1)])  # parts must add up
    rebuild(demo)
    assert len(_legs(demo, _by_key(demo, key) or 0)) == 3
    # Undo: back to one category, categorised again by the pipeline.
    back = split_txn(demo, _by_key(demo, key) or 0, [])
    assert len(_legs(demo, back)) == 2


def test_deleted_line_stays_deleted_after_rebuild(demo: Connection) -> None:
    tid = _one(demo, "SHELL OIL%")
    key = _key(demo, tid)
    delete_txn_by_user(demo, tid)
    assert _by_key(demo, key) is None
    rebuild(demo)
    assert _by_key(demo, key) is None


def test_manual_cash_entry_survives_rebuild(demo: Connection) -> None:
    tid = add_manual(
        demo,
        date=dt.date(2026, 9, 12),
        account="Assets:Cash:Wallet",
        amount=Decimal("-18.50"),
        category="Expenses:Food:Dining",
        description="Taco truck",
    )
    assert _legs(demo, tid) == [("Assets:Cash:Wallet", -1850), ("Expenses:Food:Dining", 1850)]
    edited = edit_txn(demo, tid, amount=Decimal("-20"))
    assert _legs(demo, edited)[0][1] == -2000
    rebuild(demo)
    assert (
        demo.execute(
            text("SELECT COUNT(*) FROM transactions WHERE description = 'Taco truck'")
        ).scalar()
        == 1
    )


def test_transfer_pairs_cannot_be_edited_as_one_line(demo: Connection) -> None:
    pair = demo.execute(
        text(
            "SELECT txn_id FROM postings WHERE source_key IS NOT NULL GROUP BY txn_id"
            " HAVING COUNT(*) = 2 LIMIT 1"
        )
    ).scalar_one()
    with pytest.raises(LedgerError):
        edit_txn(demo, pair, amount=Decimal("-1"))


def test_api_round_trip(env: object) -> None:
    from pf.api.app import app

    with TestClient(app) as c:
        r = c.post(
            "/api/transactions",
            json={
                "date": "2026-09-12",
                "account": "Assets:Cash:Wallet",
                "amount": "-12",
                "category": "Expenses:Food:Coffee",
                "description": "Coffee",
            },
        )
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        r = c.patch(f"/api/transactions/{tid}", json={"description": "Flat white"})
        assert r.status_code == 200 and r.json()["description"] == "Flat white"
        assert c.delete(f"/api/transactions/{r.json()['id']}").json() == {"ok": True}
        assert (
            c.post(
                "/api/transactions",
                json={
                    "date": "x",
                    "account": "Assets:Cash:Wallet",
                    "amount": "1",
                    "category": "Income:Other",
                    "description": "",
                },
            ).status_code
            == 422
        )


def test_category_drilldown_includes_every_split_category(env: object) -> None:
    from pf.api.app import app

    with TestClient(app) as c:
        import json

        doc = {
            "keel_connector": 1,
            "source": "plaid",
            "account_id": "cash",
            "institution": "Test Bank",
            "type": "Assets",
            "currency": "USD",
            "mask": "1234",
            "transactions": [
                {
                    "id": "purchase",
                    "date": "2026-08-15",
                    "amount": "-100",
                    "description": "Shared purchase",
                }
            ],
        }
        imported = c.post(
            "/api/import",
            files=[("files", ("cash.json", json.dumps(doc).encode(), "application/json"))],
        )
        assert imported.json()[0]["status"] == "imported", imported.text
        t = c.get("/api/transactions").json()["items"][0]
        split = c.post(
            f"/api/transactions/{t['id']}/split",
            json={
                "parts": [
                    {"account": "Expenses:Food:Groceries", "amount": "30"},
                    {"account": "Expenses:Housing:Rent", "amount": "70"},
                ]
            },
        )
        assert split.status_code == 200, split.text
        page = c.get(
            "/api/transactions", params={"month": "2026-08", "category": "Expenses:Housing"}
        ).json()
        assert page["total"] == 1
        assert page["items"][0]["id"] == split.json()["id"]
        assert (
            c.get(
                "/api/transactions", params={"month": "2026-09", "category": "Expenses:Housing"}
            ).json()["total"]
            == 0
        )
