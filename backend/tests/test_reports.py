from __future__ import annotations

import datetime as dt
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Connection, text

from pf.db import make_engine, migrate
from pf.ledger.categorize import set_category
from pf.ledger.decisions import export_decisions, import_decisions
from pf.ledger.importing import import_file
from pf.ledger.rebuild import rebuild
from pf.ledger.valuation import set_value, value_of
from pf.reports.balance_sheet import balance_sheet, networth_series
from pf.reports.cashflow import cashflow
from pf.reports.common import add_months, month_end, month_range
from pf.reports.insights import insights
from pf.reports.lighthouses import lighthouses, targets, years_to_reach
from tests.conftest import DEMO

DECISIONS = DEMO.parent / "decisions.yaml"


def seed(conn: Connection, decisions: bool = True) -> None:
    for f in sorted(DEMO.glob("*")):
        import_file(conn, f)
    if decisions:
        import_decisions(conn, yaml.safe_load(DECISIONS.read_text()))


@pytest.fixture
def demo(conn: Connection) -> Connection:
    seed(conn)
    return conn


# ---------------------------------------------------------------- lighthouses math
def test_lighthouse_years_match_the_video_example() -> None:
    """巫师's example: save $20k/yr at 10%, spend $60k/yr: lighthouses at 200k / 600k / 1.5M,
    reached after ~8, ~7 and ~8 more years (annual compounding, closed form)."""
    t = targets(20_000, 60_000, 0.10, 0.04)
    assert t == pytest.approx([200_000, 600_000, 1_500_000])
    legs = [
        years_to_reach(0, 20_000, 0.10, t[0], periods_per_year=1),
        years_to_reach(t[0], 20_000, 0.10, t[1], periods_per_year=1),
        years_to_reach(t[1], 20_000, 0.10, t[2], periods_per_year=1),
    ]
    assert legs == pytest.approx([7.27, 7.27, 7.91], abs=0.01)
    assert sum(legs) == pytest.approx(22.45, abs=0.05)  # 22-year-old graduate -> ~45


@pytest.mark.parametrize(
    "start,contrib,rate,target,expected",
    [
        (100, 0, 0.1, 50, 0.0),  # already there
        (0, 0, 0.0, 100, None),  # no savings, no growth: never
        (0, 1200, 0.0, 1200, 1.0),  # zero return: linear
        (1000, -10_000, 0.05, 2000, None),  # dissaving faster than growth: never
    ],
)
def test_years_to_reach_edges(start, contrib, rate, target, expected) -> None:  # type: ignore[no-untyped-def]
    got = years_to_reach(start, contrib, rate, target)
    assert got == (pytest.approx(expected) if expected is not None else None)


# ---------------------------------------------------------------- accounting identities
def test_savings_equals_networth_change_minus_valuation(demo: Connection) -> None:
    """Every month: income - expenses == Δ net worth - unrealised valuation change.
    Any double count, missing transfer leg or FX slip breaks this identity."""
    months = month_range("2024-12", "2026-09")
    flows = {f.month: f for f in cashflow(demo, months[0], months[-1])}
    for m in months:
        prev = balance_sheet(demo, month_end(add_months(m, -1)).isoformat()).net_worth
        now = balance_sheet(demo, month_end(m).isoformat()).net_worth
        unreal = -demo.execute(
            text(
                "SELECT COALESCE(SUM(amount),0) FROM v_postings WHERE account ="
                " 'Income:Investment:Unrealized' AND date BETWEEN :s AND :e"
            ),
            {"s": f"{m}-01", "e": month_end(m).isoformat()},
        ).scalar_one()
        assert flows[m].savings == now - prev - unreal, m


def test_cashflow_matches_demo_generator(demo: Connection) -> None:
    sep = cashflow(demo, "2026-09", "2026-09")[0]
    assert sep.income_by["Income:Salary"] == 2 * 421055
    assert sep.expenses_by["Expenses:Housing:Rent"] == 235000
    assert "Assets:Vanguard:Brokerage" not in sep.expenses_by  # investing is not spending
    assert sep.savings_rate is not None and 0.3 < sep.savings_rate < 0.7


def test_balance_sheet_and_series(demo: Connection) -> None:
    bs = balance_sheet(demo, "2026-09-30")
    assert bs.by_class["brokerage"] == 3465695  # = the 2026-09-30 valuation mark
    assert bs.net_worth == bs.assets + bs.liabilities
    series = networth_series(demo, "2026-07", "2026-09")
    assert [p.month for p in series] == ["2026-07", "2026-08", "2026-09"]
    assert series[-1].net_worth == bs.net_worth


def test_lighthouses_on_demo(demo: Connection) -> None:
    lh = lighthouses(demo, "2026-09")
    assert lh.investable == balance_sheet(demo, "2026-09-30").by_class["cash"] + 3465695
    assert 0.04 < lh.expected_return < 0.08  # blend of cash 4% and brokerage 8%
    p = [x.progress for x in lh.lighthouses]
    assert all(0 < v < 1 for v in p)
    assert [x.years_to_go for x in lh.lighthouses] == sorted(x.years_to_go for x in lh.lighthouses)  # type: ignore[type-var]


# ---------------------------------------------------------------- valuations
def test_valuation_marks_stay_exact_when_history_changes(env: Path) -> None:
    eng = make_engine(env / "v.db")
    migrate(eng)
    with eng.begin() as c:
        seed(c, decisions=False)
        acct = "Assets:Vanguard:Brokerage"
        set_value(c, acct, dt.date(2026, 1, 31), D("10000"))
        set_value(c, acct, dt.date(2026, 6, 30), D("20000"))
        assert value_of(c, acct, "2026-06-30") == D("20000")
        # Contributions are categorised INTO the account only now -> marks must still hold.
        vanguard = c.execute(
            text("SELECT id FROM transactions WHERE description LIKE 'VANGUARD BUY%' LIMIT 1")
        ).scalar_one()
        set_category(c, vanguard, acct, remember="rule")
        assert value_of(c, acct, "2026-01-31") == D("10000")
        assert value_of(c, acct, "2026-06-30") == D("20000")
        # Editing the earlier mark doesn't disturb the later one.
        set_value(c, acct, dt.date(2026, 1, 31), D("12000"))
        assert value_of(c, acct, "2026-06-30") == D("20000")
        rebuild(c)
        assert value_of(c, acct, "2026-01-31") == D("12000")
        assert value_of(c, acct, "2026-06-30") == D("20000")


def test_valuation_is_not_cash_flow(demo: Connection) -> None:
    for f in cashflow(demo, "2025-01", "2026-09"):
        assert "Income:Investment:Unrealized" not in f.income_by


def test_decisions_roundtrip_reproduces_reports(demo: Connection, env: Path) -> None:
    exported = export_decisions(demo)
    eng = make_engine(env / "copy.db")
    migrate(eng)
    with eng.begin() as c2:
        seed(c2, decisions=False)
        import_decisions(c2, exported)
        for m in ("2025-06", "2026-09"):
            assert (
                balance_sheet(c2, month_end(m).isoformat()).net_worth
                == balance_sheet(demo, month_end(m).isoformat()).net_worth
            )
            assert cashflow(c2, m, m)[0].expenses == cashflow(demo, m, m)[0].expenses


# ---------------------------------------------------------------- insights
def kinds(conn: Connection, month: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for i in insights(conn, month):
        out.setdefault(i.kind, []).append(i.title)
    return out


def test_insights_detect_planted_events(demo: Connection) -> None:
    assert any("netflix" in t for t in kinds(demo, "2025-09").get("price_change", []))
    feb = kinds(demo, "2026-02")
    assert any("TIFFANY" in t for t in feb.get("large_one_off", []))
    assert "category_spike" in feb
    assert any("claude.ai" in t for t in kinds(demo, "2026-06").get("new_subscription", []))
    assert "idle_cash" in kinds(demo, "2026-09")


def test_insights_no_false_alarms_in_a_quiet_month(demo: Connection) -> None:
    k = kinds(demo, "2026-03")
    assert "large_one_off" not in k and "price_change" not in k


def test_insights_run_for_every_month(demo: Connection) -> None:
    for m in month_range("2024-11", "2026-10"):
        insights(demo, m)  # never raises, even with < 12 months of history
