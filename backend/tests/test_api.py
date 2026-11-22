from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pf.api.app import app
from tests.conftest import DEMO


def test_annual_transaction_scope_has_monthly_fx_and_inclusive_end(
    env: Path, no_fx_baseline: None
) -> None:
    from sqlalchemy import text

    from pf.db import make_engine
    from pf.ledger.core import ensure_account

    with TestClient(app) as c:
        with make_engine().begin() as conn:
            ensure_account(conn, "Assets:Test:CNY", currency="CNY")
        for date, amount, currency, category in [
            ("2025-12-31", "-20", "USD", "Expenses:Food"),
            ("2026-09-01", "-500", "CNY", "Expenses:Food"),
            ("2026-09-30", "-80", "USD", "Expenses:Food"),
            ("2026-10-01", "-90", "USD", "Expenses:Food"),
            ("2026-09-15", "1000", "USD", "Income:Salary"),
        ]:
            r = c.post(
                "/api/transactions",
                json={
                    "date": date,
                    "account": "Assets:Test:" + currency,
                    "amount": amount,
                    "commodity": currency,
                    "category": category,
                    "description": "Scope test",
                },
            )
            assert r.status_code == 200, r.text
        with make_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO prices(date, base, quote, rate, source) VALUES ('2026-09-01','CNY','USD','0.13','test'), ('2026-09-30','CNY','USD','0.14','test')"
                )
            )
        params = {"month": "2026", "category": "Expenses", "end_date": "2026-09-30", "limit": 1}
        first = c.get("/api/transactions", params=params).json()
        second = c.get("/api/transactions", params={**params, "offset": 1}).json()
        assert first["total"] == second["total"] == 2
        assert first["items"][0]["date"] == "2026-09-30"
        assert first["items"][0]["base_rate"] == "1"
        assert second["items"][0]["commodity"] == "CNY"
        assert second["items"][0]["base_rate"] == "0.14"
        assert c.get("/api/transactions", params={"month": "2025"}).json()["total"] == 1
        assert (
            c.get("/api/transactions", params={"month": "2026", "category": "Income"}).json()[
                "total"
            ]
            == 1
        )
        with make_engine().begin() as conn:
            conn.execute(text("DELETE FROM prices WHERE source='test'"))
        page = c.get("/api/transactions", params={**params, "offset": 1}).json()
        assert page["items"][0]["base_rate"] is None


def test_api_import_review_categorize_flow(env: Path) -> None:
    with TestClient(app) as c:
        files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(DEMO.glob("*"))]
        res = c.post("/api/import", files=files).json()
        assert all(r["status"] == "imported" for r in res), [
            r for r in res if r["status"] != "imported"
        ]
        again = c.post("/api/import", files=files[:3]).json()
        assert {r["status"] for r in again} == {"already_imported"}

        summary = c.get("/api/review/summary").json()
        assert summary["uncategorized"] == 47

        page = c.get("/api/transactions", params={"q": "OAKWOOD", "limit": 5}).json()
        assert page["total"] == 24
        rent = page["items"][0]
        assert rent["amount"] == "-2350.00" and rent["counter_account"] == "Expenses:Uncategorized"

        out = c.post(
            f"/api/transactions/{rent['id']}/category",
            json={"category": "Expenses:Housing:Rent", "remember": "rule"},
        ).json()
        assert out["also_updated"] == 23
        assert c.get("/api/review/summary").json()["uncategorized"] == 23  # Vanguard left

        transfers = c.get("/api/transactions", params={"kind": "transfer"}).json()
        assert transfers["total"] == 44
        t = transfers["items"][0]
        assert t["account"].startswith("Assets:") and t["counter_account"].startswith(
            "Liabilities:"
        )

        assert c.get("/api/reconcile").json() == []
        accts = {a["name"]: a for a in c.get("/api/accounts").json()}
        assert accts["Assets:Chase:Checking"]["balance"] == "67923.86"
        assert "Expenses:Housing:Rent" in c.get("/api/categories").json()


def test_api_unknown_format_returns_pending(env: Path) -> None:
    """Statement-like files wait to be taught; obvious non-statements are rejected outright."""
    statement = "日期,摘要,金额,余额\n2026-09-01,超市,-10.00,90.00\n2026-09-02,工资,20.00,110.00\n"
    with TestClient(app) as c:
        res = c.post(
            "/api/import",
            files=[
                ("files", ("bank.csv", statement.encode(), "text/csv")),
                ("files", ("junk.csv", b"a,b\n1,2\n", "text/csv")),
            ],
        ).json()
        assert res[0]["status"] == "unknown_format" and res[0]["pending_sha"]
        assert res[1]["status"] == "error" and "does not look like a statement" in res[1]["message"]
        assert [p["filename"] for p in c.get("/api/onboarding/pending").json()] == ["bank.csv"]


def test_home_keeps_selected_month_without_imported_transactions(env: Path) -> None:
    from pf.reports.common import add_months

    with TestClient(app) as c:
        files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(DEMO.glob("*"))]
        c.post("/api/import", files=files)
        last = c.get("/api/reports/range").json()["last"]
        selected = add_months(last, 1)
        home = c.get("/api/home", params={"month": selected}).json()
        assert home["has_data"]
        assert home["sentence"]["month"] == selected
        assert home["sentence"]["income"] == "0.00"
        assert home["sentence"]["expenses"] == "0.00"
        assert all(
            row["amount"] == "0.00"
            for row in c.get("/api/reports/income", params={"month": selected}).json()
        )


def test_reports_api_on_full_demo(env: Path) -> None:
    import yaml

    from pf.db import make_engine
    from pf.ledger.decisions import import_decisions
    from pf.ledger.importing import import_file

    with make_engine().begin() as conn:
        for f in sorted(DEMO.glob("*")):
            import_file(conn, f)
        import_decisions(conn, yaml.safe_load((DEMO.parent / "decisions.yaml").read_text()))

    with TestClient(app) as c:
        assert c.get("/api/reports/range").json() == {"first": "2024-11", "last": "2026-10"}
        ov = c.get("/api/reports/overview", params={"month": "2026-09"}).json()
        assert ov["current"]["income"] == "8422.96"
        assert ov["current"]["net_worth"] == "94951.20"
        assert len(ov["series"]) == 12 and ov["series"][-1]["month"] == "2026-09"
        assert {i["kind"] for i in ov["insights"]} >= {"idle_cash", "networth_change"}
        assert ov["top_expenses"][0]["category"] == "Expenses:Housing"

        cf = c.get("/api/reports/cashflow", params={"start": "2026-07", "end": "2026-09"}).json()
        assert cf["months"] == ["2026-07", "2026-08", "2026-09"]
        rent = next(r for r in cf["expenses"] if r["category"] == "Expenses:Housing")
        assert rent["values"][0].startswith("2")

        tree = c.get("/api/reports/expenses", params={"month": "2026-09"}).json()
        assert any(n["category"] == "Expenses:Food:Groceries" and n["depth"] == 3 for n in tree)

        # Income uses the same selected month and category drilldown as spending. Own-account
        # transfers and valuation changes must not inflate either side of the summary.
        from decimal import Decimal

        income = c.get("/api/reports/income", params={"month": "2026-09"})
        assert income.status_code == 200, income.text
        sources = [n for n in income.json() if n["depth"] == 2]
        assert sum(Decimal(n["amount"]) for n in sources) == Decimal("8422.96")
        assert all(n["category"].startswith("Income:") for n in sources)
        salary = next(n for n in sources if n["category"] == "Income:Salary")
        assert Decimal(salary["amount"]) > 0
        september = c.get(
            "/api/transactions",
            params={"month": "2026-09", "category": "Income:Salary", "limit": 2000},
        ).json()
        assert sum(Decimal(t["amount"]) for t in september["items"]) == Decimal(salary["amount"])
        august = c.get("/api/reports/income", params={"month": "2026-08"}).json()
        assert (
            next(n["amount"] for n in august if n["category"] == "Income:Salary") == salary["prev"]
        )

        sk = c.get("/api/reports/sankey", params={"month": "2026-09"}).json()
        assert "储蓄" in sk["nodes"] and "Income:Salary" in sk["nodes"]
        inflow = sum(float(x["value"]) for x in sk["links"] if x["target"] == "收入")
        outflow = sum(float(x["value"]) for x in sk["links"] if x["source"] == "收入")
        assert abs(inflow - outflow) < 0.01  # every dollar in is either spent or saved

        bs = c.get("/api/reports/balance-sheet", params={"date": "2026-09-30"}).json()
        assert bs["net_worth"] == "94951.20" and bs["by_class"]["brokerage"] == "34656.95"

        nw = c.get("/api/reports/networth").json()
        assert nw[0]["month"] == "2024-11" and nw[-1]["month"] == "2026-10"

        lh = c.get("/api/reports/lighthouses", params={"month": "2026-09"}).json()
        assert [x["id"] for x in lh["lighthouses"]] == [1, 2, 3]
        assert all(x["eta"] for x in lh["lighthouses"])

        r = c.post(
            "/api/reports/valuations",
            json={"account": "Assets:Home:House", "date": "2026-09-30", "value": "500000"},
        )
        assert r.status_code == 200
        bs2 = c.get("/api/reports/balance-sheet", params={"date": "2026-09-30"}).json()
        assert bs2["by_class"]["real_estate"] == "500000.00"
        assert (
            c.post(
                "/api/reports/valuations",
                json={"account": "Expenses:Food", "date": "2026-09-30", "value": "1"},
            ).status_code
            == 400
        )


def test_sim_api(env: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import numpy as np
    import pandas as pd

    from pf.api.routers import sim as sim_router

    idx = pd.period_range("1900-01", periods=1500, freq="M")
    rng = np.random.default_rng(0)
    h = pd.DataFrame(
        {
            "stocks": rng.normal(0.008, 0.045, 1500),
            "bonds": rng.normal(0.004, 0.02, 1500),
            "cash": 0.002,
            "infl": 0.002,
        },
        index=idx,
    )
    monkeypatch.setattr(sim_router, "history", lambda: h)
    sc = {
        "initial": 100000,
        "weights": {"stocks": 0.7, "bonds": 0.3},
        "annual_income": 90000,
        "annual_spending": 60000,
        "work_years": 15,
        "horizon_years": 40,
        "events": [{"year": 5, "amount": -50000, "label": "首付"}],
    }
    model = {"kind": "bootstrap", "window_start": "1900-01"}
    with TestClient(app) as c:
        r = c.post("/api/sim/run", json={"scenario": sc, "model": model, "paths": 500}).json()
        assert len(r["years"]) == 41 and set(r["percentiles"]) == {"p5", "p25", "p50", "p75", "p95"}
        assert (
            r["percentiles"]["p5"][-1] <= r["percentiles"]["p50"][-1] <= r["percentiles"]["p95"][-1]
        )
        assert 0 <= r["success"] <= 1 and r["ruin_by_year"] == sorted(r["ruin_by_year"])
        bad = c.post(
            "/api/sim/run",
            json={"scenario": {**sc, "weights": {"stocks": 0.5}}, "model": model, "paths": 200},
        )
        assert bad.status_code == 400
        s = c.post(
            "/api/sim/sensitivity", json={"scenario": sc, "model": model, "paths": 300}
        ).json()
        assert len(s["rows"]) == 6
        rt = c.post(
            "/api/sim/retirement",
            json={"scenario": sc, "model": model, "paths": 200, "max_years": 30, "step": 5},
        ).json()
        assert rt["years"] == [0, 5, 10, 15, 20, 25, 30]
        rp = c.post("/api/sim/replays", json={"scenario": sc, "model": model}).json()
        assert {x["start"] for x in rp} >= {"1929-09", "1966-01"}
        o = c.post(
            "/api/sim/optimize", json={"scenario": sc, "model": model, "paths": 200, "step": 0.25}
        ).json()
        assert len(o["points"]) == 15
        assert (
            c.put(
                "/api/sim/scenarios", json={"name": "买房", "scenario": sc, "model": model}
            ).status_code
            == 200
        )
        assert [x["name"] for x in c.get("/api/sim/scenarios").json()] == ["买房"]
        d = c.get("/api/sim/defaults").json()
        assert d["derived_from"] == "empty ledger"


def test_calculator_api_types_and_defaults(env: Path) -> None:
    with TestClient(app) as c:
        m = c.post("/api/calc/mortgage", json={"principal": 300000, "annual_rate": 0.06}).json()
        assert round(m["payment"], 2) == 1798.65 and m["months"] == 360
        r = c.post("/api/calc/rental", json={"price": 500000, "rent": 3000, "hold_years": 7}).json()
        assert len(r["cash_flows_by_year"]) == 7  # int field really is an int
        rb = c.post(
            "/api/calc/rent-vs-buy", json={"price": 500000, "rent": 2500, "horizon_years": 15}
        ).json()
        assert len(rb["buy_net_worth"]) == 16
        cv = c.post("/api/calc/cash-vs-loan", json={"loan": 300000, "annual_rate": 0.06}).json()
        assert 0.05 < cv["breakeven_return"] < 0.07
        assert (
            c.post("/api/calc/mortgage", json={"annual_rate": 0.06}).status_code == 422
        )  # principal required


def test_backtest_api(env: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import numpy as np
    import pandas as pd

    from pf.api.routers import tools

    rng = np.random.default_rng(1)
    px = pd.Series(
        100 * np.cumprod(1 + rng.normal(0.008, 0.05, 300)),
        index=pd.period_range("2000-01", periods=300, freq="M"),
    )
    monkeypatch.setattr(tools, "monthly_prices", lambda t: px)
    with TestClient(app) as c:
        r = c.post(
            "/api/backtest/run",
            json={"ticker": "fake", "windows": [10, 20, 99], "detail_start": "2005-01"},
        ).json()
        assert [w["years"] for w in r["windows"]] == [10, 20]  # 99y doesn't fit -> skipped
        w10 = r["windows"][0]
        assert len(w10["starts"]) == 181 and set(w10["summary"]) == {
            "dca",
            "lump",
            "dip",
            "ma",
            "smart",
        }
        assert r["detail"]["start"] == "2005-01" and len(r["detail"]["months"]) == 120
        assert len(r["detail"]["value"]["dca"]) == 120


def test_planning_defaults_offer_reviewed_basis_not_all_historical_income(env: Path) -> None:
    with TestClient(app) as c:
        for date, amount, category in [
            ("2026-09-01", "15000", "Income:Salary"),
            ("2026-08-01", "41560", "Income:国际汇款"),
            ("2026-07-01", "-28297", "Expenses:Education"),
            ("2026-09-02", "-1200", "Expenses:Food"),
            ("2026-11-01", "-1", "Expenses:Food"),
        ]:
            r = c.post(
                "/api/transactions",
                json={
                    "date": date,
                    "amount": amount,
                    "category": category,
                    "account": "Assets:Review:USD",
                    "commodity": "USD",
                    "description": "Planning basis test",
                },
            )
            assert r.status_code == 200, r.text
        d = c.get("/api/sim/defaults").json()
        assert d["facts"]["income_year"] == 56560
        assert d["planning_basis"]["excluded_income"] == 41560
        assert d["planning_basis"]["excluded_spending"] == 28297
        # Salary and food only (no remittance, no tuition), scaled from the 3 months with activity
        # to a year: a new ledger's starting plan. The review sheet keeps the observed totals.
        assert (
            d["planning_basis"]["months"] == 3 and d["planning_basis"]["suggested_income"] == 15000
        )
        assert d["scenario"]["annual_income"] == 60000
        assert d["scenario"]["annual_spending"] == 4800


def test_employment_tax_api_validation_and_nj_credit(env: Path) -> None:
    with TestClient(app) as c:
        body = {
            "gross_income": 200000,
            "retirement_contribution": 20000,
            "filing_status": "single",
            "residence": "nj",
        }
        response = c.post("/api/sim/employment-tax", json=body)
        assert response.status_code == 200, response.text
        d = response.json()
        assert d["tax_year"] == 2026 and d["nyc_tax"] == 0
        assert d["nj_credit"] <= d["nj_tax_before_credit"]
        assert d["net_income"] == round(180000 - d["total_tax"], 2)
        for invalid in [
            {"residence": "china"},
            {"work_location": "nj"},
            {"gross_income": True},
            {"gross_income": "200000"},
            {"gross_income": -1},
            {"retirement_contribution": 25000},
        ]:
            assert c.post("/api/sim/employment-tax", json={**body, **invalid}).status_code in (
                400,
                422,
            )


def test_china_and_nonresident_tax_routes(env: Path) -> None:
    with TestClient(app) as c:
        cn = c.post(
            "/api/sim/china-employment-tax",
            json={
                "gross_income_cny": 300000,
                "social_insurance_annual": 18000,
                "housing_fund_annual": 12000,
                "special_deductions_annual": 24000,
            },
        )
        assert cn.status_code == 200, cn.text
        assert cn.json()["currency"] == "CNY"
        assert cn.json()["net_income"] == round(270000 - cn.json()["income_tax"], 2)
        nr = c.post(
            "/api/sim/nonresident-employment-tax",
            json={
                "gross_income": 100000,
                "fica_exempt": True,
                "treaty_exempt_income": 5000,
                "residence": "nj",
            },
        )
        assert nr.status_code == 200, nr.text
        assert nr.json()["social_security"] == nr.json()["medicare"] == 0
        assert nr.json()["federal_standard_deduction"] == 0
        assert nr.json()["nj_credit_income_fraction"] < 1
        for path, bad in [
            ("/api/sim/china-employment-tax", {"gross_income_cny": True}),
            (
                "/api/sim/nonresident-employment-tax",
                {"gross_income": 100000, "filing_status": "married_filing_jointly"},
            ),
        ]:
            assert c.post(path, json=bad).status_code == 422


def test_cash_bridge_uses_usd_cash_minus_cards_and_calendar_months(env: Path) -> None:
    import datetime as dt

    today = dt.date.today()
    first = today.replace(day=1).isoformat()

    def month(k: int) -> str:
        y, m = divmod(today.year * 12 + today.month - 1 + k, 12)
        return f"{y:04d}-{m + 1:02d}"

    with TestClient(app) as c:
        for account, amount, category in [
            ("Assets:Bank:Checking", "10000", "Income:Salary"),
            ("Liabilities:Card:Visa", "-2000", "Expenses:Food"),
        ]:
            r = c.post(
                "/api/transactions",
                json={
                    "date": first,
                    "amount": amount,
                    "category": category,
                    "account": account,
                    "commodity": "USD",
                    "description": "bridge test",
                },
            )
            assert r.status_code == 200, r.text
        sc = {
            "initial": 0,
            "weights": {"cash": 1},
            "annual_income": 0,
            "annual_spending": 12_000,
            "work_years": 10,
            "phases": [
                {"label": "school", "start": month(0), "end": month(2), "monthly_spending": 3000}
            ],
            "events": [{"year": 0, "amount": -500, "label": "deposit", "date": month(1)}],
        }
        b = c.post("/api/sim/bridge", json={"scenario": sc, "months": 5}).json()
        assert b["cash"] == 10000 and b["cards"] == 2000 and b["start"] == 8000
        assert [r["balance"] for r in b["rows"]] == [5000, 1500, -1500, -2500, -3500]
        assert b["rows"][1]["labels"] == ["school", "deposit"]
        assert b["short_month"] == month(2) and b["low_month"] == month(4)
        bad = {**sc, "phases": [{"start": "2027-13", "end": month(1)}]}
        assert c.post("/api/sim/bridge", json={"scenario": bad}).status_code == 400
        # the same phases run through the Monte Carlo too
        assert c.post("/api/sim/run", json={"scenario": sc, "paths": 100}).status_code in (200, 409)


def test_plan_seed_from_local_yaml(env: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import shutil

    from tests.conftest import REPO

    cfg = env / "config"
    shutil.copytree(REPO / "config", cfg)
    (cfg / "plan.local.yaml").write_text(
        "phases:\n  - {label: school, start: 2027-01, end: 2027-05, monthly_spending: 3300}\n"
        "events:\n  - {label: tuition, date: 2027-01, amount: -38224}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PF_CONFIG_DIR", str(cfg))
    with TestClient(app) as c:
        c.post(
            "/api/transactions",
            json={
                "date": "2026-09-01",
                "amount": "100",
                "category": "Income:Salary",
                "account": "Assets:Bank:Checking",
                "commodity": "USD",
                "description": "seed",
            },
        )
        sc = c.get("/api/sim/defaults").json()["scenario"]
    assert sc["phases"][0]["start"] == "2027-01" and sc["phases"][0]["monthly_spending"] == 3300
    assert sc["events"][0]["date"] == "2027-01" and sc["events"][0]["amount"] == -38224
