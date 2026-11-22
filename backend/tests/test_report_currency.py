from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from pf.api.app import app
from pf.api.routers.reports import balance
from pf.db import make_engine
from pf.ledger.core import NewPosting, NewTxn, ensure_account, post
from pf.ledger.valuation import set_value
from pf.reports.balance_sheet import balance_sheet, networth_series
from pf.reports.common import Fx


def prices(conn: Connection, rows: list[tuple[str, str, str, str]]) -> None:
    conn.execute(
        text("INSERT INTO prices(date, base, quote, rate, source) VALUES (:d, :b, :q, :r, 'test')"),
        [{"d": d, "b": b, "q": q, "r": r} for d, b, q, r in rows],
    )


def transaction(
    conn: Connection,
    date: str,
    commodity: str,
    parts: list[tuple[str, str]],
    *,
    kind: str = "normal",
    status: str = "confirmed",
    description: str = "Report currency",
    account: str | None = None,
) -> int:
    total = sum((Decimal(amount) for _, amount in parts), Decimal(0))
    return post(
        conn,
        NewTxn(
            date=dt.date.fromisoformat(date),
            description=description,
            status=status,
            kind=kind,
            postings=[
                NewPosting(
                    account or f"Assets:Test:{commodity}",
                    -total,
                    commodity,
                    source_key=f"{date}:{description}:{parts}",
                ),
                *[NewPosting(category, Decimal(amount), commodity) for category, amount in parts],
            ],
        ),
    )


def test_fx_direct_inverse_usd_bridge_and_no_future_rates(conn: Connection) -> None:
    prices(
        conn,
        [
            ("2026-01-01", "USD", "CNY", "8"),
            ("2026-01-01", "BTC", "USD", "100"),
            ("2026-01-01", "USD", "EUR", "0.5"),
            ("2026-03-01", "EUR", "CNY", "20"),
        ],
    )
    usd, cny = Fx(conn), Fx(conn, "CNY")
    assert usd.rate("CNY", "2026-01-31") == Decimal("0.125")
    assert cny.rate("USD", "2026-01-31") == Decimal(8)
    assert cny.rate("BTC", "2026-01-31") == Decimal(800)
    assert cny.rate("EUR", "2026-01-31") == Decimal(16)
    assert cny.rate("EUR", "2026-03-31") == Decimal(20)
    assert cny.rate("BTC", "2025-12-31") is None
    assert cny.rate("HKD", "2026-01-31") is None
    assert cny.to_base(1000000, "BTC", "2026-01-31") == 800


def test_bank_asset_snapshot_counts_newly_imported_balance_only_once(conn: Connection) -> None:
    bank = "Example Bank"
    snapshot = f"Assets:{bank}:ReportedTotal"
    deposit = f"Assets:{bank}:Checking"
    ensure_account(conn, snapshot, currency="CNY", institution=bank, asset_class="term_deposit")
    conn.execute(
        text("UPDATE accounts SET note = 'Time deposits' WHERE name = :name"),
        {"name": snapshot},
    )
    set_value(conn, snapshot, dt.date(2026, 11, 7), Decimal("1500"), "CNY")
    ensure_account(conn, deposit, currency="CNY", institution=bank)
    transaction(conn, "2026-11-01", "CNY", [("Income:Salary", "-500")], account=deposit)
    transaction(conn, "2026-11-08", "CNY", [("Expenses:Food", "40")], account=deposit)
    fx = Fx(conn, "CNY")
    assert balance_sheet(conn, "2026-11-06", fx).assets == 50000
    assert balance_sheet(conn, "2026-11-07", fx).assets == 150000
    assert balance_sheet(conn, "2026-11-08", fx).assets == 146000
    rows = {line.account: line for line in balance(conn, "2026-11-08", "CNY").lines}
    assert rows[snapshot].note == "Time deposits"
    assert rows[snapshot].asset_class == "term_deposit"
    assert rows[deposit].note is None
    assert rows[snapshot].original_balances == {"CNY": "1000.00"}
    assert rows[deposit].original_balances == {"CNY": "460.00"}


def test_monthly_report_endpoints_use_selected_currency(env: Path) -> None:
    with make_engine().begin() as conn:
        prices(conn, [("2026-01-01", "USD", "CNY", "7")])
        transaction(conn, "2026-01-05", "USD", [("Expenses:Food", "100")])
        transaction(conn, "2026-01-06", "USD", [("Income:Salary", "-250")])
    with TestClient(app) as client:
        usd = client.get("/api/reports/overview", params={"month": "2026-01"}).json()
        assert usd["currency"] == "USD" and usd["current"]["expenses"] == "100.00"
        overview = client.get(
            "/api/reports/overview", params={"month": "2026-01", "currency": "CNY"}
        ).json()
        assert overview["currency"] == "CNY" and overview["current"]["expenses"] == "700.00"
        assert overview["current"]["income"] == "1750.00"
        for path, amount in (("expenses", "700.00"), ("income", "1750.00")):
            rows = client.get(
                f"/api/reports/{path}", params={"month": "2026-01", "currency": "CNY"}
            ).json()
            assert rows[0]["amount"] == amount
        pulse = client.get("/api/pulse", params={"as_of": "2026-01-07", "currency": "CNY"}).json()
        assert pulse["currency"] == "CNY" and pulse["spent"] == "700.00"
        cash = client.get(
            "/api/cashline", params={"start": "2026-01-05", "end": "2026-01-06", "currency": "CNY"}
        ).json()
        assert cash[0]["outflow"] == "700.00" and cash[1]["inflow"] == "1750.00"


def test_balance_sheet_keeps_native_balances_across_currency_switch(
    conn: Connection, no_fx_baseline: None
) -> None:
    date = dt.date(2026, 11, 7)
    set_value(conn, "Assets:Example:Checking", date, Decimal("123.45"), "CNY")
    set_value(conn, "Assets:Example:Brokerage", date, Decimal("56.78"), "USD")
    set_value(conn, "Liabilities:Example:Card", date, Decimal("-8.90"), "USD")
    report = balance(conn, date.isoformat(), "USD")
    assert report.missing_fx == ["CNY"]
    original = {
        "Assets:Example:Checking": {"CNY": "123.45"},
        "Assets:Example:Brokerage": {"USD": "56.78"},
        "Liabilities:Example:Card": {"USD": "-8.90"},
    }
    assert {line.account: line.original_balances for line in report.lines} == original
    prices(conn, [(date.isoformat(), "USD", "CNY", "7")])
    for currency, expected in (("USD", "65.52"), ("CNY", "458.61")):
        report = balance(conn, date.isoformat(), currency)
        assert report.net_worth == expected
        assert report.missing_fx == []
        assert {line.account: line.original_balances for line in report.lines} == original


@pytest.mark.parametrize(
    "currency,expected", [("USD", ["12.50", "13.00"]), ("CNY", ["100.00", "65.00"])]
)
def test_reports_use_selected_currency_and_default_stays_usd(
    env: Path, currency: str, expected: list[str]
) -> None:
    with make_engine().begin() as conn:
        prices(conn, [("2026-01-31", "USD", "CNY", "8"), ("2026-02-28", "USD", "CNY", "5")])
        transaction(conn, "2026-01-05", "USD", [("Expenses:Food", "10")])
        transaction(conn, "2026-01-06", "CNY", [("Expenses:Food", "20")])
        transaction(conn, "2026-02-05", "USD", [("Expenses:Food", "10")])
        transaction(conn, "2026-02-06", "CNY", [("Expenses:Food", "15")])
        legacy = networth_series(conn, "2026-01", "2026-02")
        selected = networth_series(conn, "2026-01", "2026-02", Fx(conn, currency))
        assert legacy[-1].net_worth == -2700
        assert selected[-1].net_worth == (-2700 if currency == "USD" else -13500)
    with TestClient(app) as client:
        params = {"start": "2026-01", "end": "2026-02", "currency": currency}
        cashflow = client.get("/api/reports/cashflow", params=params).json()
        assert cashflow["currency"] == currency and cashflow["missing_fx"] == []
        assert cashflow["expenses_total"] == expected
        assert cashflow["original_flows"] == [
            {"currency": "CNY", "income": "0", "expenses": "35.00", "savings": "-35.00"},
            {"currency": "USD", "income": "0", "expenses": "20.00", "savings": "-20.00"},
        ]
        balance = client.get(
            "/api/reports/balance-sheet", params={"date": "2026-02-28", "currency": currency}
        ).json()
        assert balance["currency"] == currency and balance["missing_fx"] == []
        assert balance["net_worth"] == ("-27.00" if currency == "USD" else "-135.00")
        series = client.get("/api/reports/networth", params=params).json()
        assert series[-1]["currency"] == currency and series[-1]["missing_fx"] == []
        assert series[-1]["net_worth"] == balance["net_worth"]
        assert client.get(
            "/api/reports/cashflow", params={"start": "2026-01", "end": "2026-02"}
        ).json()["expenses_total"] == ["12.50", "13.00"]


def test_cashflow_detail_summary_selects_split_categories_refunds_and_all_pages(env: Path) -> None:
    with make_engine().begin() as conn:
        prices(conn, [("2026-01-31", "CNY", "USD", "0.125"), ("2026-02-28", "CNY", "USD", "0.2")])
        split_id = transaction(
            conn,
            "2026-01-01",
            "CNY",
            [("Expenses:Food:Groceries", "40"), ("Expenses:Travel", "60")],
        )
        transaction(conn, "2026-01-02", "CNY", [("Expenses:Food:Groceries", "-8")])
        transaction(conn, "2026-02-01", "CNY", [("Expenses:Food:Groceries", "10")])
        transaction(conn, "2026-02-02", "USD", [("Expenses:Food:Dining", "5")])
        transaction(conn, "2026-02-03", "USD", [("Income:Salary", "-100")])
        transaction(conn, "2026-02-04", "USD", [("Expenses:Food:Dining", "999")], kind="opening")
        transaction(conn, "2026-02-05", "USD", [("Income:Salary", "-999")], kind="adjustment")
        transaction(conn, "2026-02-06", "USD", [("Assets:Other:USD", "200")], kind="transfer")
    with TestClient(app) as client:
        params = {"month": "2026", "category": "Expenses:Food", "limit": 1, "include_summary": True}
        page = client.get("/api/transactions", params=params).json()
        assert page["total"] == 4 and len(page["items"]) == 1
        assert page["currency"] == "USD" and page["missing_fx"] == []
        assert page["display_total"] == "11.00"
        assert page["original_totals"] == {"CNY": "42.00", "USD": "5.00"}
        assert page["items"][0]["display_currency"] == "USD"
        assert page["items"][0]["base_rate"] == page["items"][0]["display_rate"] == "1"
        next_page = client.get("/api/transactions", params={**params, "offset": 1}).json()
        assert next_page["total"] == 4
        assert next_page["display_total"] is None and next_page["original_totals"] == {}
        all_items = client.get("/api/transactions", params={**params, "limit": 2000}).json()[
            "items"
        ]
        split = next(item for item in all_items if item["id"] == split_id)
        assert split["amount"] == "-100.00"
        assert split["report_original_amounts"]["CNY"] == "40.00"
        assert (
            next(
                item["report_original_amounts"]["CNY"]
                for item in all_items
                if item["date"] == "2026-01-02"
            )
            == "-8.00"
        )
        assert split["parts"] == [
            {"account": "Expenses:Food:Groceries", "amount": "40.00"},
            {"account": "Expenses:Travel", "amount": "60.00"},
        ]
        report = client.get(
            "/api/reports/cashflow", params={"start": "2026-01", "end": "2026-02"}
        ).json()
        assert (
            next(row["total"] for row in report["expenses"] if row["category"] == "Expenses:Food")
            == page["display_total"]
        )
        savings = client.get(
            "/api/transactions",
            params={"month": "2026", "cashflow_only": True, "include_summary": True},
        ).json()
        assert savings["total"] == 5
        assert savings["display_total"] == "81.50"
        assert savings["original_totals"] == {"CNY": "-102.00", "USD": "95.00"}
        income = client.get(
            "/api/transactions",
            params={"month": "2026", "category": "Income", "include_summary": True},
        ).json()
        assert income["total"] == 1 and income["display_total"] == "100.00"
        cny = client.get("/api/transactions", params={**params, "currency": "CNY"}).json()
        assert cny["display_total"] == "67.00"
        assert cny["items"][0]["display_rate"] == "5"
        assert cny["items"][0]["base_rate"] == "1"


def test_transfer_with_expense_leg_counts_only_the_fee(env: Path) -> None:
    with make_engine().begin() as conn:
        transaction(
            conn,
            "2026-01-01",
            "USD",
            [("Assets:Other:USD", "200"), ("Expenses:Bank:Fees", "2")],
            kind="transfer",
        )
    with TestClient(app) as client:
        params = {"cashflow_only": True, "include_summary": True}
        page = client.get("/api/transactions", params=params).json()
        assert page["total"] == 1 and page["display_total"] == "-2.00"
        assert page["items"][0]["amount"] == "-202.00"
        assert page["items"][0]["report_original_amounts"]["USD"] == "-2.00"
        fees = client.get(
            "/api/transactions", params={**params, "category": "Expenses:Bank"}
        ).json()
        assert fees["display_total"] == fees["items"][0]["report_original_amounts"]["USD"] == "2.00"


def test_summary_groups_monthly_leaf_totals_before_rounding(env: Path) -> None:
    with make_engine().begin() as conn:
        prices(conn, [("2026-01-31", "CNY", "USD", "0.5"), ("2026-02-28", "CNY", "USD", "0.25")])
        for day in ("01", "02"):
            transaction(conn, f"2026-01-{day}", "CNY", [("Expenses:Food:Groceries", "0.01")])
        transaction(conn, "2026-01-03", "CNY", [("Expenses:Food:Dining", "0.01")])
        transaction(conn, "2026-02-01", "CNY", [("Expenses:Food:Groceries", "0.02")])
    with TestClient(app) as client:
        report = client.get(
            "/api/reports/cashflow", params={"start": "2026-01", "end": "2026-02"}
        ).json()
        page = client.get(
            "/api/transactions",
            params={"month": "2026", "category": "Expenses:Food", "include_summary": True},
        ).json()
        assert report["expenses_total"] == ["0.01", "0.00"]
        assert page["display_total"] == "0.01"
        assert page["original_totals"] == {"CNY": "0.05"}


def test_general_summary_keeps_statement_sign_and_every_filter(env: Path) -> None:
    with make_engine().begin() as conn:
        prices(conn, [("2026-01-02", "CNY", "USD", "0.5")])
        transaction(
            conn,
            "2026-01-01",
            "CNY",
            [("Expenses:Food", "10")],
            description="Keep",
            status="pending",
        )
        transaction(
            conn,
            "2026-01-02",
            "USD",
            [("Income:Salary", "-20")],
            description="Keep",
            status="pending",
        )
        transaction(
            conn,
            "2026-01-03",
            "USD",
            [("Expenses:Food", "1")],
            description="Keep",
            status="pending",
        )
        transaction(
            conn,
            "2026-01-01",
            "USD",
            [("Expenses:Food", "500")],
            description="Skip",
            status="pending",
        )
        transaction(conn, "2026-01-01", "USD", [("Expenses:Food", "500")], description="Keep")
    with TestClient(app) as client:
        params = {
            "q": "Keep",
            "status": "pending",
            "end_date": "2026-01-02",
            "limit": 1,
            "include_summary": True,
        }
        page = client.get("/api/transactions", params=params).json()
        assert page["total"] == 2
        assert page["display_total"] == "15.00"
        assert page["original_totals"] == {"CNY": "-10.00", "USD": "20.00"}
        account = client.get(
            "/api/transactions", params={**params, "account": "Assets:Test:CNY"}
        ).json()
        assert account["total"] == 1 and account["display_total"] == "-5.00"
        default = client.get(
            "/api/transactions", params={**params, "include_summary": False}
        ).json()
        assert default["total"] == 2 and default["display_total"] is None


def test_partial_month_excludes_future_transactions_and_exchange_rates(env: Path) -> None:
    with make_engine().begin() as conn:
        prices(
            conn,
            [
                ("2026-01-31", "CNY", "USD", "0.125"),
                ("2026-02-03", "CNY", "USD", "0.2"),
                ("2026-02-28", "CNY", "USD", "0.25"),
            ],
        )
        for date in ("2026-01-20", "2026-02-06", "2026-02-07"):
            transaction(conn, date, "CNY", [("Expenses:Food", "40")])
    with TestClient(app) as client:
        report = client.get(
            "/api/reports/cashflow",
            params={"start": "2026-01", "end": "2026-02", "end_date": "2026-02-06"},
        ).json()
        page = client.get(
            "/api/transactions",
            params={
                "month": "2026",
                "category": "Expenses",
                "end_date": "2026-02-06",
                "include_summary": True,
            },
        ).json()
        assert report["expenses_total"] == ["5.00", "8.00"]
        assert page["total"] == 2 and page["display_total"] == "13.00"
        assert [t["display_rate"] for t in page["items"]] == ["0.2", "0.125"]
        assert [t["base_rate"] for t in page["items"]] == ["0.2", "0.125"]


def test_missing_fx_is_reported_without_inventing_transaction_total(
    env: Path, no_fx_baseline: None
) -> None:
    with make_engine().begin() as conn:
        transaction(conn, "2026-01-01", "CNY", [("Expenses:Food", "10")])
        prices(conn, [("2026-02-01", "CNY", "USD", "0.5")])
    with TestClient(app) as client:
        params = {"month": "2026-01", "category": "Expenses", "include_summary": True}
        page = client.get("/api/transactions", params=params).json()
        assert page["display_total"] is None and page["missing_fx"] == ["CNY"]
        assert page["original_totals"] == {"CNY": "10.00"}
        assert page["items"][0]["base_rate"] is None
        assert page["items"][0]["display_rate"] is None
        for endpoint, query in [
            ("cashflow", {"start": "2026-01", "end": "2026-01"}),
            ("balance-sheet", {"date": "2026-01-31"}),
        ]:
            result = client.get(f"/api/reports/{endpoint}", params=query).json()
            assert result["currency"] == "USD" and result["missing_fx"] == ["CNY"]
        series = client.get(
            "/api/reports/networth", params={"start": "2026-01", "end": "2026-02"}
        ).json()
        assert [p["missing_fx"] for p in series] == [["CNY"], []]
        native = client.get("/api/transactions", params={**params, "currency": "CNY"}).json()
        assert native["display_total"] == "10.00" and native["missing_fx"] == []
        assert native["items"][0]["display_rate"] == "1"
        assert native["items"][0]["base_rate"] is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/reports/cashflow",
        "/api/reports/balance-sheet",
        "/api/reports/networth",
        "/api/transactions",
    ],
)
def test_report_currency_rejects_unsupported_values(env: Path, endpoint: str) -> None:
    with TestClient(app) as client:
        assert client.get(endpoint, params={"currency": "EUR"}).status_code == 422


def test_cross_currency_transfer_fee_row_matches_report_scope(env: Path) -> None:
    with make_engine().begin() as conn:
        prices(conn, [("2026-01-31", "CNY", "USD", "0.125")])
        post(
            conn,
            NewTxn(
                date=dt.date(2026, 1, 5),
                description="FX transfer with fee",
                kind="transfer",
                weight_commodity="USD",
                postings=[
                    NewPosting("Assets:Bank:USD", Decimal("-101"), "USD", source_key="fx-source"),
                    NewPosting("Assets:Bank:CNY", Decimal("800"), "CNY", weight=Decimal("100")),
                    NewPosting("Expenses:Bank:Fees", Decimal("8"), "CNY", weight=Decimal("1")),
                ],
            ),
        )
    with TestClient(app) as client:
        params = {"cashflow_only": True, "include_summary": True}
        page = client.get("/api/transactions", params=params).json()
        assert page["display_total"] == "-1.00"
        item = page["items"][0]
        assert item["report_original_amounts"] == {"CNY": "-8.00"}
        assert Decimal(item["report_display_amount"]) == -1
        cny = client.get(
            "/api/transactions", params={**params, "currency": "CNY", "category": "Expenses"}
        ).json()
        assert cny["display_total"] == "8.00"
        assert Decimal(cny["items"][0]["report_display_amount"]) == 8


def test_zero_native_subtotals_do_not_require_fx(env: Path, no_fx_baseline: None) -> None:
    with make_engine().begin() as conn:
        transaction(conn, "2026-01-01", "CNY", [("Expenses:Food", "10")])
        transaction(conn, "2026-01-02", "CNY", [("Expenses:Food", "-10")])
        transaction(conn, "2026-01-02", "USD", [("Expenses:Food", "20")])
    with TestClient(app) as client:
        report = client.get(
            "/api/reports/cashflow", params={"start": "2026-01", "end": "2026-01"}
        ).json()
        assert report["missing_fx"] == [] and report["expenses_total"] == ["20.00"]
        balance = client.get("/api/reports/balance-sheet", params={"date": "2026-01-31"}).json()
        assert balance["missing_fx"] == [] and balance["net_worth"] == "-20.00"
        page = client.get(
            "/api/transactions", params={"category": "Expenses", "include_summary": True}
        ).json()
        assert page["display_total"] == "20.00"
        assert all(
            item["report_display_amount"] is None
            for item in page["items"]
            if item["commodity"] == "CNY"
        )


def test_bundled_rates_convert_before_any_download(conn: Connection) -> None:
    """A fresh install has no downloaded rates: CNY still converts from the bundled table."""
    from pf.reports.common import Fx

    fx = Fx(conn, "USD")
    rate = fx.rate("CNY", "2026-10-15")
    assert rate is not None and Decimal("0.12") < rate < Decimal("0.16")
    back = Fx(conn, "CNY").rate("USD", "2026-10-15")
    assert back is not None and Decimal("6") < back < Decimal("8.5")
