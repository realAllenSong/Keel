"""Schwab, Fidelity, IBKR, 宁波银行 and OKX: parsers, and an end-to-end eval on synthetic
households whose ground truth is known (fixtures/brokers/synth.py)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Connection, text

from pf.importers.base import ImportError_
from pf.importers.brokers import (
    DIVIDENDS,
    FEES,
    INTEREST,
    SKIP,
    TAXES,
    TRANSFER,
    FidelityImporter,
    IbkrImporter,
    SchwabImporter,
    classify,
)
from pf.importers.cn_banks import ChinaMerchantsBankImporter, NingboBankImporter, _cmb_statement
from pf.importers.okx import OkxImporter
from pf.importers.registry import detect
from pf.ledger.decisions import export_decisions
from pf.ledger.importing import import_file
from pf.ledger.onboarding import claim_pending
from pf.ledger.rebuild import rebuild
from pf.ledger.reconcile import reconcile
from pf.ledger.valuation import set_value, value_of
from pf.reports.balance_sheet import balance_sheet
from pf.reports.common import Fx

BROKERS = Path(__file__).resolve().parents[2] / "fixtures" / "brokers"
FILES = BROKERS / "statements"


def _synth():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("broker_synth", BROKERS / "synth.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["broker_synth"] = mod
    spec.loader.exec_module(mod)
    return mod


def _file(pattern: str) -> Path:
    (path,) = FILES.glob(pattern)
    return path


# ---------------------------------------------------------------- classification
@pytest.mark.parametrize(
    ("action", "qty", "symbol", "kind"),
    [
        ("Qualified Dividend", None, "VTI", DIVIDENDS),
        ("Reinvest Dividend", None, "VTI", DIVIDENDS),
        ("Reinvest Shares", Decimal("0.1"), "VTI", SKIP),
        ("REINVESTMENT FIDELITY 500 INDEX FUND (FXAIX) (Cash)", Decimal("1"), "FXAIX", SKIP),
        ("DIVIDEND RECEIVED VANGUARD (VTI) (Cash)", None, "VTI", DIVIDENDS),
        ("TAX-EXEMPT DIVIDEND MUNI FUND (Cash)", None, "VTEB", DIVIDENDS),
        ("Long Term Cap Gain", None, "VFIAX", DIVIDENDS),
        ("Foreign Tax Paid", None, "NESN", TAXES),
        ("NRA Tax Adj", None, "", TAXES),
        ("Bank Interest", None, "", INTEREST),
        ("INTEREST EARNED FDIC INSURED DEPOSIT (Cash)", None, "", INTEREST),
        ("Margin Interest", None, "", FEES),
        ("ADR Mgmt Fee", None, "NESN", FEES),
        ("Buy", Decimal("5"), "VTI", SKIP),
        ("YOU SOLD APPLE INC (AAPL) (Cash)", Decimal("-3"), "AAPL", SKIP),
        ("REDEMPTION FROM CORE ACCOUNT (SPAXX)", Decimal("10"), "SPAXX", SKIP),
        ("Stock Split", Decimal("10"), "NVDA", SKIP),
        ("MoneyLink Transfer", None, "", TRANSFER),
        ("Wire Funds", None, "", TRANSFER),
        ("Electronic Funds Transfer Received (Cash)", None, "", TRANSFER),
        ("Journal", None, "", TRANSFER),
        ("Misc Cash Entry", None, "", "unknown"),  # kept for review
        ("Some Corporate Action", Decimal("3"), "XYZ", SKIP),  # shares, no cash
    ],
)
def test_classify(action: str, qty: Decimal | None, symbol: str, kind: str) -> None:
    assert classify(action, qty, symbol) == kind


# ---------------------------------------------------------------- parsers
def test_schwab_history_books_flows_and_skips_trades() -> None:
    st = SchwabImporter().parse(_file("Individual_XXX234_Transactions_*.csv"))
    assert st.account_hint == "234" and st.asset_class == "brokerage" and not st.valuations
    kinds = {(r.description.split(" · ")[0], r.category) for r in st.rows}
    assert ("MoneyLink Transfer", None) in kinds and ("Reinvest Dividend", DIVIDENDS) in kinds
    assert ("Bank Interest", INTEREST) in kinds and ("Foreign Tax Paid", TAXES) in kinds
    assert not [r for r in st.rows if r.description.startswith(("Buy", "Reinvest Shares"))]
    interest = next(r for r in st.rows if r.category == INTEREST)
    assert interest.date.day == 28  # "09/28/2026 as of 09/27/2026": the day cash moved


def test_schwab_positions_mark_and_multi_account_refused(tmp_path: Path) -> None:
    src = _file("Individual-Positions-*.csv")
    st = SchwabImporter().parse(src)
    assert st.rows == [] and st.account_hint == "234"
    expected = json.loads((BROKERS / "expected.json").read_text())["Assets:Schwab:Brokerage-234"]
    assert st.valuations == [(dt.date(2026, 9, 30), Decimal(expected["marks"]["2026-09-30"]))]
    twice = tmp_path / "All-Accounts-Positions-2026-09-30.csv"
    body = src.read_text()
    twice.write_text(body + body.split("\n", 2)[2])
    with pytest.raises(ImportError_, match="single account"):
        SchwabImporter().parse(twice)


def test_schwab_legacy_layout_with_title_and_total(tmp_path: Path) -> None:
    synth = _synth()
    synth.household(tmp_path, seed=1)  # odd seeds write the legacy layout
    (path,) = tmp_path.glob("Individual_XXX234_Transactions_*.csv")
    assert path.read_text().startswith('"Transactions  for account')
    st = detect(path).parse(path)
    assert st.importer == "schwab" and st.account_hint == "234" and st.rows


def test_fidelity_history_positions_and_multi_account(tmp_path: Path) -> None:
    hist = FidelityImporter().parse(_file("Accounts_History.csv"))
    assert hist.account_hint == "6789" and hist.asset_class == "retirement"  # ROTH IRA
    assert {r.category for r in hist.rows} == {None, DIVIDENDS}
    assert all(r.amount > 0 for r in hist.rows if r.category is None)  # contributions in
    pos = FidelityImporter().parse(_file("Portfolio_Positions_*.csv"))
    assert pos.account_hint == "6789" and pos.valuations[0][0] == dt.date(2026, 9, 30)
    mixed = tmp_path / "Accounts_History.csv"
    lines = _file("Accounts_History.csv").read_text().splitlines()
    lines.insert(4, lines[3].replace("Z23456789", "X99999999"))
    mixed.write_text("\n".join(lines))
    with pytest.raises(ImportError_, match="2 Fidelity accounts"):
        FidelityImporter().parse(mixed)


def test_ibkr_sections_fx_and_nav_marks() -> None:
    st = IbkrImporter().parse(_file("U7654321_*.csv"))
    assert st.account_hint == "4321" and st.currency == "USD"
    assert [d for d, _ in st.valuations] == [dt.date(2025, 12, 31), dt.date(2026, 9, 30)]
    eur = [r for r in st.rows if "ASML" in r.description]
    assert [r.amount for r in eur] == [Decimal("21.70")]  # EUR 20.00 at the statement's 1.0850
    assert not [r for r in st.rows if r.amount == Decimal("999.99")]  # Total rows are not lines
    assert any(r.description.startswith("Withdrawal") and r.amount < 0 for r in st.rows)


def test_cmb_statement_validates_running_balances() -> None:
    pages = [
        "Transaction Statement of China Merchants Bank\n账号：00001234\n"
        "2026-10-01 CNY 1,000.00 1,100.00 转账收入 示例公司\n"
        "2026-10-01 CNY -900.00 200.00 快捷支付 蚂蚁基金\n",
        "2026-10-02 CNY 0.01 200.01 账户结息\n",
    ]
    stmt = _cmb_statement(pages, "test.pdf")
    assert stmt.account_hint == "1234" and stmt.currency == "CNY"
    assert [r.amount for r in stmt.rows] == [Decimal("1000"), Decimal("-900"), Decimal(".01")]
    assert stmt.assertions == [(dt.date(2026, 10, 2), Decimal("200.01"))]
    assert stmt.rows[-1].category == "Income:Interest"
    with pytest.raises(ImportError_):
        _cmb_statement([pages[0], pages[1].replace("200.01", "210.01")], "test.pdf")


def test_cmb_new_statement_updates_asset_after_manual_observation(
    conn: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    title = "Transaction Statement of China Merchants Bank\n账号：****1234\n"
    opening = "2026-10-01 CNY 1,000.00 1,100.00 转账收入 示例公司\n"
    old = tmp_path / "first.pdf"
    old.write_text(title + "2026-10-01 -- 2026-10-01\n" + opening)
    recent = tmp_path / "recent.pdf"
    recent.write_text(
        title
        + "2026-10-01 -- 2026-10-05\n"
        + opening
        + "2026-10-03 CNY -100.00 1,000.00 消费 示例商户\n"
    )
    monkeypatch.setattr(
        ChinaMerchantsBankImporter,
        "parse",
        lambda _self, path: _cmb_statement([path.read_text()], path.name),
    )
    report = import_file(conn, old, importer="cmb")
    assert report.account is not None
    set_value(conn, report.account, dt.date(2026, 10, 2), Decimal("1100.07"), "CNY")
    latest = import_file(conn, recent, importer="cmb")
    assert (latest.new, latest.duplicates) == (1, 1)
    assert latest.period == ("2026-10-01", "2026-10-05")
    fx = Fx(conn, "CNY")
    assert balance_sheet(conn, "2026-10-02", fx).assets == 110007
    assert balance_sheet(conn, "2026-10-05", fx).assets == 100000


def test_ningbo_xlsx_and_signed_gbk_csv() -> None:
    for name, hint in (
        ("宁波银行交易明细_1234_*.xlsx", "1234"),
        ("nbcb_detail_5678_*.csv", "5678"),
    ):
        path = _file(name)
        assert detect(path).name == "ningbo"
        st = NingboBankImporter().parse(path)
        assert st.account_hint == hint and st.currency == "CNY"
        salary = [r for r in st.rows if r.description.startswith("代发工资")]
        rent = [r for r in st.rows if r.payee == "李四"]
        assert salary and all(r.amount == Decimal("15800.00") for r in salary)
        assert rent and all(r.amount == Decimal("-3000.00") for r in rent)
        assert len(st.assertions) == 1  # running balance chains (newest-first CSV too)
        assert st.valuations == st.assertions


def test_okx_keeps_stablecoin_edges_only() -> None:
    st = OkxImporter().parse(_file("OKX_funding_bills_*.csv"))
    assert st.default_account == "Assets:OKX:Crypto" and st.asset_class == "crypto"
    assert not [r for r in st.rows if "Trade" in r.description or "Transfer" in r.description]
    assert not [r for r in st.rows if "BTC" in r.description]  # no price, left to the mark
    fee = [r for r in st.rows if r.category == FEES]
    assert [r.amount for r in fee] == [Decimal("-1")]
    snap = OkxImporter().parse(_file("OKX_assets_*.csv"))
    assert snap.rows == [] and snap.valuations[0][0] == dt.date(2026, 9, 30)


# ---------------------------------------------------------------- end-to-end eval
def _drop_everything(conn: Connection, folder: Path) -> dict[str, str]:
    """What the inbox does: import each file; claim each pending one into its suggested account
    (the rest of the same source then imports by itself). Returns file -> importer."""
    seen: dict[str, str] = {}
    for path in sorted(folder.iterdir()):
        r = import_file(conn, path, archive=False)
        if r.status == "needs_account":
            assert r.pending_sha and r.suggested_account
            r = claim_pending(conn, r.pending_sha, r.suggested_account)
        assert r.status in ("imported", "already_imported"), (path.name, r.status, r.message)
        seen[path.name] = r.importer or ""
    return seen


def _sum(conn: Connection, account: str, category: str) -> Decimal:
    """Ledger-sign amount booked to `account` against `category` (income positive)."""
    v = conn.execute(
        text(
            """SELECT COALESCE(SUM(s.amount), 0) FROM v_postings s
               JOIN v_postings c ON c.txn_id = s.txn_id AND c.account = :c
               WHERE s.account = :a"""
        ),
        {"a": account, "c": category},
    ).scalar_one()
    return Decimal(int(v)).scaleb(-2)


def _evaluate(conn: Connection, truth: dict[str, dict[str, object]]) -> None:
    for account, t in truth.items():
        assert _sum(conn, account, DIVIDENDS) == Decimal(str(t["dividends"])), account
        assert _sum(conn, account, INTEREST) == Decimal(str(t["interest"])), account
        assert -_sum(conn, account, FEES) == Decimal(str(t["fees"])), account
        assert -_sum(conn, account, TAXES) == Decimal(str(t["taxes"])), account
        for date, value in dict(t["marks"]).items():  # type: ignore[call-overload]
            assert value_of(conn, account, date) == Decimal(value), (account, date)
        transfers = conn.execute(
            text(
                "SELECT COUNT(DISTINCT txn_id) FROM v_postings WHERE account = :a AND kind = 'transfer'"
            ),
            {"a": account},
        ).scalar_one()
        assert transfers == t["paired"], account
        open_lines = conn.execute(
            text(
                """SELECT COUNT(*) FROM v_postings s JOIN v_postings c ON c.txn_id = s.txn_id
                   AND c.account IN ('Income:Uncategorized', 'Expenses:Uncategorized')
                   WHERE s.account = :a AND s.source_key IS NOT NULL"""
            ),
            {"a": account},
        ).scalar_one()
        if t["importer"] != "ningbo":  # bank spending is left for review on purpose
            assert open_lines == t["unpaired"], account
        if t["closing"]:
            date, bal = t["closing"]  # type: ignore[misc]
            assert value_of(conn, account, date) == Decimal(bal), account
    assert reconcile(conn) == []
    # Brokers' value comes from statement marks; the first one is the opening, not a gain.
    unrealized_first = conn.execute(
        text(
            """SELECT COUNT(*) FROM transactions t WHERE t.kind = 'adjustment'
               AND t.description LIKE 'Opening balance%'"""
        )
    ).scalar_one()
    assert unrealized_first == sum(1 for t in truth.values() if t["marks"])


def test_eval_committed_household(conn: Connection) -> None:
    truth = json.loads((BROKERS / "expected.json").read_text(encoding="utf-8"))
    seen = _drop_everything(conn, FILES)
    assert {v for k, v in seen.items() if not k.startswith("Chase")} == {
        "schwab",
        "fidelity",
        "ibkr",
        "ningbo",
        "okx",
    }
    _evaluate(conn, truth)
    bs = balance_sheet(conn, "2026-09-30")
    assert {"brokerage", "retirement", "crypto", "cash"} <= set(bs.by_class)
    # IBKR's prior NAV is the opening: no unrealized gain before the statement starts.
    ibkr = "Assets:IBKR:Brokerage-4321"
    assert value_of(conn, ibkr, "2025-12-31") == Decimal(truth[ibkr]["marks"]["2025-12-31"])

    # Dropping the same files again changes nothing.
    for path in FILES.iterdir():
        assert import_file(conn, path, archive=False).status == "already_imported"
    # Statement marks are re-derived from raw/, not exported as your decisions.
    assert export_decisions(conn)["valuations"] == []


@pytest.mark.parametrize("seed", range(1, 9))
def test_eval_random_households(conn: Connection, tmp_path: Path, seed: int) -> None:
    synth = _synth()
    truth = synth.household(tmp_path / "files", seed=seed)
    _drop_everything(conn, tmp_path / "files")
    _evaluate(conn, {k: v.as_json() for k, v in truth.items()})


def test_rebuild_reproduces_broker_values(conn: Connection) -> None:
    for path in sorted(FILES.iterdir()):
        r = import_file(conn, path)
        if r.status == "needs_account":
            assert r.pending_sha and r.suggested_account
            claim_pending(conn, r.pending_sha, r.suggested_account)
    before = balance_sheet(conn, "2026-09-30")
    rebuild(conn)
    after = balance_sheet(conn, "2026-09-30")
    assert [(x.account, x.balance) for x in after.lines] == [
        (x.account, x.balance) for x in before.lines
    ]


def test_your_mark_beats_a_reimported_statement(conn: Connection) -> None:
    path = _file("Individual-Positions-*.csv")
    r = import_file(conn, path, account="Assets:Schwab:Brokerage-234", archive=False)
    assert r.status == "imported"
    set_value(conn, "Assets:Schwab:Brokerage-234", dt.date(2026, 9, 30), Decimal("12345.00"))
    conn.execute(text("DELETE FROM import_files"))  # pretend it was never imported
    import_file(conn, path, account="Assets:Schwab:Brokerage-234", archive=False)
    assert value_of(conn, "Assets:Schwab:Brokerage-234", "2026-09-30") == Decimal("12345.00")
    assert export_decisions(conn)["valuations"][0]["value"] == "12345.00"


def test_pending_brokers_suggest_their_kind_of_account(env: Path) -> None:
    from fastapi.testclient import TestClient

    from pf.api.app import app

    with TestClient(app) as c:
        for pattern in ("Individual-Positions-*.csv", "Portfolio_Positions_*.csv", "U7654321_*"):
            path = _file(pattern)
            c.post("/api/import", files=[("files", (path.name, path.read_bytes()))])
        listed = c.get("/api/onboarding/pending").json()
    assert sorted(p["suggested_account"] for p in listed) == [
        "Assets:Fidelity:Retirement-6789",
        "Assets:IBKR:Brokerage-4321",
        "Assets:Schwab:Brokerage-234",
    ]
