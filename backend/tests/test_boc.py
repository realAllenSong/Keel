"""中国银行交易流水 PDF: locked by the code shown in the app, then read as a ruled table."""

from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from pf.api.app import app
from pf.importers.archive import extract_statement, is_locked, provider_for_member
from pf.importers.archive_passwords import ArchivePasswords
from pf.importers.cn_banks import BankOfChinaImporter
from pf.ledger.importing import import_file

CN = Path(__file__).resolve().parents[2] / "fixtures" / "cn"
LOCKED = CN / "boc_debit_202609.pdf"
PASSWORD = "135790"


def _plain(tmp_path: Path) -> Path:
    return extract_statement(LOCKED, tmp_path, PASSWORD)


def test_reads_every_line_with_signed_amounts_and_a_chained_balance(tmp_path: Path) -> None:
    plain = _plain(tmp_path)
    imp = BankOfChinaImporter()
    assert imp.sniff(plain, "")
    stmt = imp.parse(plain)
    assert stmt.institution == "Bank of China" and stmt.currency == "CNY"
    assert len(stmt.rows) == 10
    assert stmt.rows[0].amount == Decimal("8600.00")
    assert stmt.rows[1].amount == Decimal("-36.50")
    # Wrapped cells join back into one line.
    assert stmt.rows[2].payee == "支付宝（中国）网络技术有限公司"
    assert stmt.rows[3].description == "转账汇款 林小雨 房租九月"
    assert stmt.rows[7].category == "Income:Interest"
    assert stmt.account_hint == "1230" and stmt.holder == "示例用户"
    assert stmt.assertions and stmt.assertions[0][1] == Decimal("7056.07")
    assert stmt.valuations[0][0].isoformat() == "2026-09-30"


def test_locked_pdf_waits_for_its_code_then_imports(conn, env) -> None:
    assert is_locked(LOCKED) and provider_for_member("中国银行交易流水.pdf") == "boc"
    report = import_file(conn, LOCKED)
    assert report.status == "needs_password"
    assert conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar() == 0
    report = import_file(conn, LOCKED, password=PASSWORD)
    assert report.status == "imported" and report.new == 10
    assert report.account == "Assets:Bank of China:Checking-1230"
    assert import_file(conn, LOCKED).status == "already_imported"


def test_wrong_code_keeps_it_pending_and_a_saved_code_unlocks_any_name(conn, env) -> None:
    with TestClient(app) as c:
        first = c.post(
            "/api/import", files=[("files", ("statement.pdf", LOCKED.read_bytes()))]
        ).json()[0]
        assert first["status"] == "needs_password"
        endpoint = f"/api/onboarding/pending/{first['pending_sha']}/unlock"
        assert c.post(endpoint, json={"password": "000000"}).status_code == 400
    # A code saved under 中国银行 opens an attachment whose name says nothing about the bank.
    ArchivePasswords.remember("boc", PASSWORD)
    assert import_file(conn, LOCKED).status == "imported"
