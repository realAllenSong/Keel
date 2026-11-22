"""支付宝 / 微信支付: dedicated parsers, password-protected zips, and wallets kept out of net worth."""

from __future__ import annotations

import csv
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from pf.api.app import app
from pf.db import make_engine
from pf.importers.base import read_head
from pf.importers.cn_wallets import AlipayImporter, WeChatImporter
from pf.importers.registry import detect
from pf.ledger.importing import import_file
from pf.reports.balance_sheet import balance_sheet
from pf.reports.cashflow import cashflow

CN = Path(__file__).resolve().parents[2] / "fixtures" / "cn"
PASSWORD = "246810"


def test_alipay_skips_closed_and_neutral_rows_and_keeps_hints() -> None:
    rows = AlipayImporter().parse(CN / "alipay_record_202609.csv").rows
    assert [(r.date.isoformat(), r.amount) for r in rows] == [
        ("2026-09-28", Decimal("-32.00")),
        ("2026-09-25", Decimal("-4.00")),
        ("2026-09-21", Decimal("-128.50")),
        ("2026-09-10", Decimal("200.00")),  # 收入
        ("2026-09-03", Decimal("-28.00")),
    ]  # 交易关闭 (never paid) and 不计收支 (余额宝 transfer) are gone
    assert rows[0].payee == "示例面馆" and rows[0].category_hint == "餐饮美食"
    assert rows[0].memo == "花呗" and rows[0].ref == "AP0001"  # exporter's tab padding stripped


def test_alipay_books_yield_and_only_refunds_for_counted_purchases(
    conn: Connection, tmp_path: Path
) -> None:
    bill = tmp_path / "支付宝.csv"
    rows = [
        ("P1", "衣服", "服饰装扮", "支出", "90", "交易成功"),
        ("P1_R1", "衣服", "退款", "不计收支", "15", "退款成功"),
        ("P2", "耳机", "数码电器", "支出", "30", "交易关闭"),
        ("P2_R2", "耳机", "退款", "不计收支", "30", "退款成功"),
        ("UNKNOWN_R3", "未知订单", "退款", "不计收支", "5", "退款成功"),
        ("I1", "余额宝-2026.09.01-收益发放", "投资理财", "不计收支", "1.25", "交易成功"),
        ("T1", "余额宝-单次转入", "投资理财", "不计收支", "1000", "交易成功"),
    ]
    with bill.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            (
                "交易时间",
                "交易订单号",
                "商品说明",
                "交易分类",
                "收/支",
                "金额",
                "交易状态",
                "交易对方",
            )
        )
        w.writerows(("2026-09-02", *r, "示例商户") for r in rows)
    assert import_file(conn, bill).new == 3
    assert import_file(conn, bill).status == "already_imported"
    september = cashflow(conn, "2026-09", "2026-09")[0]
    assert september.original_income == {"CNY": Decimal("1.25")}
    assert september.original_expenses == {"CNY": Decimal("75")}


@pytest.mark.parametrize("name", ["wechat_bill_202609.csv", "wechat_bill_202609.xlsx"])
def test_wechat_csv_and_xlsx_net_refunds_and_skip_neutral(name: str) -> None:
    rows = WeChatImporter().parse(CN / name).rows
    assert [(r.description, r.amount) for r in rows] == [
        ("饮料", Decimal("-6.50")),
        ("扫二维码付款", Decimal("-45.00")),
        ("T恤", Decimal("-100.00")),  # ¥120 with ¥20 refunded
        ("微信红包", Decimal("88.00")),
    ]  # fully refunded dinner, its refund row and the 零钱提现 ("/") are gone


@pytest.mark.parametrize(
    ("name", "importer"),
    [
        ("alipay_record_202609.csv", "alipay"),
        ("wechat_bill_202609.csv", "wechat"),
        ("wechat_bill_202609.xlsx", "wechat"),
    ],
)
def test_detection_is_unambiguous(env: Path, name: str, importer: str) -> None:
    assert detect(CN / name).name == importer
    assert read_head(CN / name)  # GBK preamble decodes


def test_wallet_imports_without_asking_and_stays_out_of_net_worth(conn: Connection) -> None:
    r = import_file(conn, CN / "alipay_record_202609.csv")
    assert (r.status, r.account, r.new) == ("imported", "Assets:Alipay:demo", 5)
    assert import_file(conn, CN / "wechat_bill_202609.xlsx").account == "Assets:WeChat:示例用户"
    # Spending counts (converted from CNY at the month's rate) ...
    conn.execute(
        text(
            "INSERT INTO prices(date, base, quote, rate, source) VALUES ('2026-09-01', 'USD', 'CNY', '7.1', 'test')"
        )
    )
    month = cashflow(conn, "2026-09", "2026-09")[0]
    assert month.expenses > 0 and month.income > 0
    # ... but the wallets' running balance is not money the user holds.
    bs = balance_sheet(conn, "2026-09-30")
    assert not [
        line for line in bs.lines if line.account.startswith(("Assets:Alipay", "Assets:WeChat"))
    ]
    note = conn.execute(
        text("SELECT note FROM transactions WHERE description = '牛肉面'")
    ).scalar_one()
    assert note == "花呗"
    suggested = conn.execute(
        text(
            "SELECT a.name FROM transactions t JOIN postings p ON p.txn_id = t.id"
            " JOIN accounts a ON a.id = p.account_id"
            " WHERE t.description = '拿铁' AND a.name LIKE 'Expenses:%'"
        )
    ).scalar_one()
    assert suggested == "Expenses:Food:Dining"  # from Alipay's own 交易分类


def test_locked_zip_waits_for_password_then_imports_once(env: Path) -> None:
    with TestClient(app) as c:
        zipped = (CN / "alipay_record_202609.zip").read_bytes()
        first = c.post("/api/import", files=[("files", ("alipay.zip", zipped))]).json()[0]
        assert first["status"] == "needs_password" and "支付宝交易明细" in first["message"]
        sha = first["pending_sha"]
        listed = c.get("/api/onboarding/pending").json()
        assert [(p["reason"], p["institution"]) for p in listed] == [("needs_password", "Alipay")]

        wrong = c.post(f"/api/onboarding/pending/{sha}/unlock", json={"password": "000000"})
        assert wrong.status_code == 400 and "解压密码不对" in wrong.json()["detail"]
        assert c.get("/api/onboarding/pending").json()[0]["reason"] == "needs_password"

        ok = c.post(f"/api/onboarding/pending/{sha}/unlock", json={"password": f" {PASSWORD} "})
        assert ok.status_code == 200
        assert (ok.json()["status"], ok.json()["new"]) == ("imported", 5)
        assert c.get("/api/onboarding/pending").json() == []

        again = c.post("/api/import", files=[("files", ("alipay.zip", zipped))]).json()[0]
        assert again["status"] == "already_imported"  # no second password prompt
        # The CSV inside was archived like any other statement.
        assert any(p.suffix == ".csv" for p in (env / "raw").rglob("*"))


def test_downloads_watcher_parks_statement_zips_and_ignores_other_zips(env: Path) -> None:
    import zipfile

    from pf.ops.inbox import scan_watch_dirs

    downloads = env / "downloads"
    downloads.mkdir()
    shutil.copy(CN / "wechat_bill_202609.zip", downloads)
    with zipfile.ZipFile(downloads / "photos.zip", "w") as zf:
        zf.writestr("a.jpg", b"not a statement")
    result = scan_watch_dirs(wait_settle=False)
    assert [(r.file, r.status) for r in result.imported] == [
        ("wechat_bill_202609.zip", "needs_password")
    ]
    assert "photos.zip" in result.skipped
    with make_engine().begin() as conn:
        assert (
            conn.execute(text("SELECT institution FROM pending_imports")).scalar_one() == "WeChat"
        )
