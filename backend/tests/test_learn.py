"""The local classifier suggests categories from your confirmed lines (no network, no model)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, text

from pf.ledger.categorize import set_category
from pf.ledger.importing import import_file
from pf.ledger.learn import Learner, clean

HEADER = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"


def card(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    path.write_text(HEADER + "".join(f"{d},{d},{desc},,Sale,{amt},\n" for d, desc, amt in rows))
    return path


def test_clean_drops_terminal_noise() -> None:
    assert clean("SQ *BLUE BOTTLE 0423 #12", None) == "SQ BLUE BOTTLE"
    assert clean("TST* JOE'S PIZZA 0004", "Joe's") == "JOE TST JOE PIZZA"


def test_learner_votes_by_similarity_and_direction() -> None:
    m = Learner(
        [
            (clean("BLUE BOTTLE COFFEE"), "Expenses:Food:Coffee", True),
            (clean("BLUE BOTTLE COFFEE SF"), "Expenses:Food:Coffee", True),
            (clean("WHOLE FOODS MARKET"), "Expenses:Food:Groceries", True),
            (clean("WHOLE FOODS MKT"), "Expenses:Food:Groceries", True),
            (clean("ACME PAYROLL"), "Income:Salary", False),
        ]
    )
    assert m.suggest(clean("SQ *BLUE BOTTLE 0423"), True) == "Expenses:Food:Coffee"
    assert m.suggest(clean("WHOLEFDS MKT 10234"), True) == "Expenses:Food:Groceries"
    assert m.suggest(clean("ACME PAYROLL"), True) is None  # only money-in examples look like it
    assert m.suggest(clean("ZEPHYR QUANTUM"), True) is None  # nothing close enough


def test_imports_get_learned_suggestions_once_you_confirm_a_few(
    conn: Connection, env: Path
) -> None:
    first = import_file(
        conn,
        card(
            env / "Chase1111_a.CSV",
            [(f"09/{d:02d}/2026", f"OBSCURE NOODLE BAR #{d}", "-12.00") for d in range(1, 7)],
        ),
        account="Liabilities:Chase:Card-1111",
    )
    # You file the first six under Dining (as the review screen does).
    for tid in first.txn_ids:
        set_category(conn, tid, "Expenses:Food:Dining", remember="none")
    second = import_file(
        conn,
        card(env / "Chase1111_b.CSV", [("10/02/2026", "OBSCURE NOODLE BAR #88", "-14.00")]),
        account="Liabilities:Chase:Card-1111",
    )
    row = conn.execute(
        text(
            "SELECT t.status, t.category_source, a.name FROM transactions t JOIN postings p ON p.txn_id = t.id"
            " JOIN accounts a ON a.id = p.account_id WHERE t.id = :t AND p.source_key IS NULL"
        ),
        {"t": second.txn_ids[0]},
    ).one()
    assert tuple(row) == ("confirmed", "learned", "Expenses:Food:Dining")


def test_person_transfers_are_not_guessed() -> None:
    from pf.ledger.categorize import is_person_transfer

    for desc, hint in [
        ("Transfer", "转账红包"),
        ("转账", None),
        ("Zelle payment to Will Liu Conf# X", None),
        ("转账备注:微信转账", None),
    ]:
        assert is_person_transfer(desc, hint), desc
    for desc in ["WHOLE FOODS MARKET", "Wire Transfer Fee", "ANNUAL MEMBERSHIP FEE"]:
        assert not is_person_transfer(desc), desc
