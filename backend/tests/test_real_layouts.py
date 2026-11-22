"""Layouts checked against real exports (2026-10), rebuilt here with synthetic values:
Amex activity.xlsx, Bank of America per-card monthly CSVs, IBKR Transaction History (Chinese UI)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import Connection, text

from pf.importers.base import read_head
from pf.importers.brokers import IbkrHistoryImporter
from pf.importers.registry import detect
from pf.ledger.importing import import_file

AMEX_HEADER = ["Date", "Description", "Amount", "Extended Details", "Appears On Your Statement As",
               "Address", "City/State", "Zip Code", "Country", "Reference", "Category"]  # fmt: skip


def amex_xlsx(path: Path, card: str = "01002") -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in [
        ["Transaction Details", "American Express Gold Card / Sep 1, 2026 to Oct 30, 2026"],
        ["Prepared for", ""],
        ["SAMPLE PERSON", ""],
        ["Account Number", ""],
        [f"XXXX-XXXXXX-{card}", ""],
        ["", ""],
        AMEX_HEADER,
        [
            "10/26/2026",
            "UBER",
            "16.89",
            "x",
            "Uber Trip",
            "",
            "",
            "",
            "",
            "'R1'",
            "Transportation-Taxis & Coach",
        ],
        [
            "10/20/2026",
            "CAFE SAMPLE",
            "8.50",
            "x",
            "CAFE",
            "",
            "",
            "",
            "",
            "'R2'",
            "Restaurant-Bar & Café",
        ],
        ["10/15/2026", "AUTOPAY PAYMENT - THANK YOU", "-25.39", "", "", "", "", "", "", "'R3'", ""],
    ]:
        ws.append(row)
    wb.save(path)
    return path


def test_amex_xlsx_reads_card_from_preamble_and_charges_as_debt(env: Path) -> None:
    f = amex_xlsx(env / "activity.xlsx")
    imp = detect(f)
    st = imp.parse(f)
    assert (imp.name, st.account_hint, st.account_type) == ("amex_xlsx", "01002", "Liabilities")
    assert [r.amount for r in st.rows] == [Decimal("-16.89"), Decimal("-8.50"), Decimal("25.39")]
    assert st.rows[1].category_hint == "Restaurant-Bar & Café"


def test_hierarchical_hints_fall_back_to_first_level(conn: Connection, env: Path) -> None:
    import_file(conn, amex_xlsx(env / "activity.xlsx"), account="Liabilities:Amex:Gold")
    cats = dict(
        conn.execute(
            text(
                "SELECT t.description, a.name FROM transactions t JOIN postings p ON p.txn_id = t.id"
                " JOIN accounts a ON a.id = p.account_id WHERE a.type = 'Expenses'"
            )
        ).all()
    )
    assert cats["UBER"] == "Expenses:Transport:Rideshare"  # exact Amex category
    assert cats["CAFE SAMPLE"] == "Expenses:Food:Dining"  # 'Restaurant' first level


def test_bofa_card_files_keep_cards_apart(env: Path) -> None:
    header = "Posted Date,Reference Number,Payee,Address,Amount\n"
    hints = set()
    for name in ("August2026_0006.csv", "currentTransaction_0005.csv"):
        f = env / name
        f.write_text(header + '08/18/2026,111,"SAMPLE STORE","",-12.00\n')
        imp = detect(f)
        assert imp.name == "bofa_credit"
        hints.add(imp.parse(f).account_hint)
    assert hints == {"0006", "0005"}


IBKR_ZH = """Statement,Header,域名称,域值
Statement,Data,Title,Transaction History
Statement,Data,Period,"九月 1, 2026 - 十月 29, 2026"
总结,Header,域名称,域值
总结,Data,基础货币,USD
总结,Data,期初现金,100.0
Transaction History,Header,日期,账户,说明,交易类型,代码,数量,价格,Price Currency,总额,佣金,净额
Transaction History,Data,2026-10-08,U***1234,QQQ 现金红利 USD 0.75 每股,股息,QQQ,-,-,-,0.14,-,0.14
Transaction History,Data,2026-10-08,U***1234,QQQ 现金红利 - US Tax,外国预扣税,QQQ,-,-,-,-0.01,-,-0.01
Transaction History,Data,2026-09-02,U***1234,INVESCO QQQ TRUST,买,QQQ,0.1897,527.13,USD,-99.996561,-0.999974336,-100.996535336
Transaction History,Data,2026-09-01,U***1234,电子资金转账,存款,-,-,-,-,100.0,-,100.0
"""


def test_ibkr_transaction_history_in_chinese(env: Path) -> None:
    f = env / "U0001234.TRANSACTIONS.20260901.20261029.csv"
    f.write_text(IBKR_ZH, encoding="utf-8")
    assert IbkrHistoryImporter().sniff(f, read_head(f))
    assert detect(f).name == "ibkr_history"
    st = IbkrHistoryImporter().parse(f)
    assert (st.currency, st.account_hint) == ("USD", "1234")
    got = [(r.date.isoformat(), r.amount, r.category) for r in st.rows]
    assert got == [
        ("2026-10-08", Decimal("0.14"), "Income:Dividends"),
        ("2026-10-08", Decimal("-0.01"), "Expenses:Taxes"),
        ("2026-09-01", Decimal("100.00"), None),  # deposit: left for transfer pairing
    ]  # the buy moves value inside the account and is skipped
