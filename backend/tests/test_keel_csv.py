"""A statement an agent transcribed (from a PDF or a screenshot) goes through the usual pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Connection, text

from pf.importers.base import ImportError_
from pf.ledger.importing import import_file

CARD = """# keel statement
# institution: Testcard
# kind: card
# account: ...0003
date,description,amount,balance
2026-10-02,COFFEE SHOP,-4.50,-4.50
2026-10-03,"BOOKS, INC",-100.00,-104.50
2026-10-05,PAYMENT THANK YOU,104.50,0.00
"""


def test_transcribed_card_statement_books_checks_and_is_not_imported_twice(
    conn: Connection, tmp_path: Path
) -> None:
    f = tmp_path / "oct.csv"
    f.write_text(CARD)
    r = import_file(conn, f)
    assert (r.status, r.account, r.new, r.discrepancies) == (
        "imported",
        "Liabilities:Testcard:Card-0003",
        3,
        [],
    )
    # The same lines again in another file (a re-read of the same PDF): nothing new.
    g = tmp_path / "oct-again.csv"
    g.write_text(CARD + "\n")
    assert import_file(conn, g).new == 0
    assert (
        conn.execute(
            text("SELECT COUNT(*) FROM transactions WHERE kind IN ('normal', 'transfer')")
        ).scalar_one()
        == 3
    )


def test_a_misread_figure_shows_as_a_balance_difference(conn: Connection, tmp_path: Path) -> None:
    f = tmp_path / "bad.csv"
    f.write_text(CARD.replace("-100.00,-104.50", "-10.00,-104.50"))  # 100 read as 10
    r = import_file(conn, f)
    assert r.status == "imported" and r.discrepancies  # Keel's total and the statement's disagree


def test_bank_defaults_and_bad_lines(conn: Connection, tmp_path: Path) -> None:
    f = tmp_path / "bank.csv"
    f.write_text(
        '# keel statement\n# institution: Oldbank\nDate,Description,Amount\n2026-10-05,PAYROLL,"2,000.00"\n'
    )
    assert import_file(conn, f).account == "Assets:Oldbank:Checking"
    bad = tmp_path / "bad.csv"
    bad.write_text("# keel statement\ndate,description,amount\n10/05/2026,X,1\n")
    with pytest.raises(ImportError_, match="YYYY-MM-DD"):
        import_file(conn, bad)
    # A plain CSV without the first line is not claimed by this importer.
    from pf.importers.keel_csv import KeelCsvImporter

    assert not KeelCsvImporter().sniff(bad, "date,description,amount\n")
