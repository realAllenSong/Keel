from __future__ import annotations

import pytest
from sqlalchemy import Connection, text

from pf.ledger.categorize import confirm, merchant_key, set_category
from pf.ledger.importing import import_file
from pf.ledger.rebuild import rebuild
from pf.ledger.reconcile import reconcile
from tests.conftest import DEMO


def total(conn: Connection, prefix: str) -> int:
    return conn.execute(
        text("SELECT COALESCE(SUM(amount),0) FROM v_postings WHERE account LIKE :p"),
        {"p": prefix + "%"},
    ).scalar_one()


def txn_by_desc(conn: Connection, like: str) -> list[int]:
    return [
        r.id
        for r in conn.execute(
            text("SELECT id FROM transactions WHERE description LIKE :d ORDER BY date"), {"d": like}
        )
    ]


@pytest.fixture
def demo(conn: Connection) -> Connection:
    for f in sorted(DEMO.glob("*")):
        import_file(conn, f)
    return conn


def test_card_payments_become_transfers_not_spending(demo: Connection) -> None:
    n = demo.execute(text("SELECT COUNT(*) FROM transactions WHERE kind='transfer'")).scalar_one()
    assert n == 44  # 22 paid months x 2 cards
    # No payment description may remain as an expense or income line.
    leaked = demo.execute(
        text(
            "SELECT COUNT(*) FROM v_postings WHERE account_type IN ('Expenses','Income')"
            " AND (description LIKE 'CHASE CREDIT CRD AUTOPAY%' OR description LIKE 'AMEX EPAYMENT%'"
            "      OR description LIKE 'AUTOMATIC PAYMENT%' OR description LIKE 'AUTOPAY PAYMENT%')"
        )
    ).scalar_one()
    assert leaked == 0


def test_expenses_not_double_counted(demo: Connection) -> None:
    """Total expenses = card purchases + checking outflows that are not transfers."""
    card_purchases = -demo.execute(
        text(
            "SELECT SUM(amount) FROM v_postings WHERE account_type='Liabilities'"
            " AND kind='normal' AND amount < 0"
        )
    ).scalar_one()
    checking_spend = -demo.execute(
        text(
            "SELECT SUM(amount) FROM v_postings WHERE account='Assets:Chase:Checking'"
            " AND kind='normal' AND amount < 0"
        )
    ).scalar_one()
    assert total(demo, "Expenses:") == card_purchases + checking_spend


def test_seed_rules_and_hints(demo: Connection) -> None:
    row = demo.execute(
        text(
            "SELECT account, status FROM v_postings WHERE description LIKE 'WHOLEFDS%'"
            " AND account_type='Expenses' LIMIT 1"
        )
    ).one()
    assert (row.account, row.status) == ("Expenses:Food:Groceries", "confirmed")
    row = demo.execute(
        text(
            "SELECT account, status FROM v_postings WHERE description = 'NOBU NYC'"
            " AND account_type='Expenses' LIMIT 1"
        )
    ).one()
    assert (row.account, row.status) == ("Expenses:Food:Dining", "confirmed")  # bank hint, accepted


def test_user_rule_applies_retroactively_and_survives_rebuild(demo: Connection) -> None:
    rent = txn_by_desc(demo, "ZELLE PAYMENT TO OAKWOOD%")
    assert len(rent) == 24
    changed = set_category(demo, rent[0], "Expenses:Housing:Rent", payee="Oakwood", remember="rule")
    assert changed == 23
    vanguard = txn_by_desc(demo, "VANGUARD BUY%")
    set_category(demo, vanguard[0], "Assets:Vanguard:Brokerage", remember="rule")
    # One-off override that contradicts a seed rule.
    amazon = txn_by_desc(demo, "AMAZON.COM%")[0]
    set_category(demo, amazon, "Expenses:Gifts", remember="override")
    nobu = txn_by_desc(demo, "NOBU NYC")[0]
    confirm(demo, [nobu])

    def state() -> tuple:
        return (
            total(demo, "Expenses:Housing:Rent"),
            total(demo, "Assets:Vanguard"),
            total(demo, "Expenses:Gifts"),
            total(demo, "Expenses:Uncategorized"),
            demo.execute(
                text(
                    "SELECT status FROM transactions WHERE description='NOBU NYC' ORDER BY date LIMIT 1"
                )
            ).scalar_one(),
        )

    before = state()
    assert before[0] == 24 * 235000 and before[3] == 0 and before[4] == "confirmed"
    rebuild(demo)
    assert state() == before
    assert reconcile(demo) == []


def test_transfer_to_untracked_account_is_not_spending(demo: Connection) -> None:
    before = total(demo, "Expenses:")
    vanguard = txn_by_desc(demo, "VANGUARD BUY%")
    set_category(demo, vanguard[0], "Assets:Vanguard:Brokerage", remember="rule")
    assert total(demo, "Expenses:") == before - 23 * 150000
    assert total(demo, "Assets:Vanguard:Brokerage") == 23 * 150000


@pytest.mark.parametrize(
    "desc,key",
    [
        ("WHOLEFDS PRN 10234", "wholefds prn"),
        ("AMAZON.COM*AB12CD", "amazon.com"),
        ("ACME CORP PAYROLL PPD ID: 9000000001", "acme corp payroll"),
        ("TRADER JOE S #555", "trader joe s"),
        ("美团外卖 订单 202610150001", "美团外卖 订单"),
    ],
)
def test_merchant_key(desc: str, key: str) -> None:
    assert merchant_key(desc) == key


def test_short_patterns_match_whole_words_and_processor_prefixes_keep_their_star() -> None:
    from pf.ledger.categorize import Rule
    from pf.ledger.groups import group_key

    ic = Rule(pattern="ic", target="Expenses:Food:Groceries")
    assert not ic.matches("anthropic* claude sub", None, "Liabilities:X", -100)
    assert ic.matches("ic* instacart*1102", None, "Liabilities:X", -100)
    assert Rule(pattern="uber", target="x").matches("uber eats", None, "L", -1)
    assert Rule(pattern="停车", target="x").matches("停车缴费-浙b", None, "L", -1)
    assert group_key("IC* INSTACART*1102") == "ic*"
    assert group_key("CL *Chase Travel") == "cl *"
    assert group_key("TST* SANBADA-ALLSTON") == "tst*"
    assert group_key("AMAZON.COM*AB12CD") == "amazon.com"


def test_rule_filed_card_payment_still_pairs_with_the_card(conn: Connection) -> None:
    """A too-broad rule ("american" -> flights) filed a bank's "AMERICAN EXPRESS ACH PMT"
    as a flight; when the card's payment line arrives the two still become one transfer."""
    import datetime as dt

    from pf.ledger.core import NewPosting, NewTxn, ensure_account, post
    from pf.ledger.transfers import match_transfers

    bank, card = "Assets:BoA:Checking", "Liabilities:Amex:Card"
    for name in (bank, card):
        ensure_account(conn, name, currency="USD", institution=name.split(":")[1])

    def line(
        account: str, other: str, amount: str, desc: str, day: int, key: str, src: str | None
    ) -> int:
        tid = post(
            conn,
            NewTxn(
                date=dt.date(2026, 11, day),
                description=desc,
                status="confirmed" if src else "pending",
                postings=[
                    NewPosting(account, Decimal(amount), "USD", source_key=key),
                    NewPosting(other, -Decimal(amount), "USD"),
                ],
            ),
        )
        conn.execute(
            text("UPDATE transactions SET category_source=:s WHERE id=:t"), {"s": src, "t": tid}
        )
        return tid

    from decimal import Decimal

    line(
        bank,
        "Expenses:Travel:Flights",
        "-5592.99",
        "AMERICAN EXPRESS DES:ACH PMT",
        18,
        "b1",
        "rule",
    )
    got = line(card, "Income:Uncategorized", "5592.99", "AUTOPAY PAYMENT RECEIVED", 19, "c1", None)
    assert match_transfers(conn, [got])
    kinds = conn.execute(text("SELECT kind FROM transactions ORDER BY id")).scalars().all()
    assert kinds == ["transfer"]  # both halves merged into one transfer
    assert total(conn, "Expenses:Travel") == 0

    # A rule's category stands when neither side reads like a payment.
    lunch = line(bank, "Expenses:Food:Dining", "-42.00", "SWEETGREEN", 20, "b2", "rule")
    back = line(card, "Income:Uncategorized", "42.00", "SPLITWISE CREDIT", 20, "c2", None)
    assert not match_transfers(conn, [back])
    kinds = conn.execute(
        text("SELECT kind FROM transactions WHERE id IN (:a,:b)"), {"a": lunch, "b": back}
    )
    assert kinds.scalars().all() == ["normal", "normal"]
