from __future__ import annotations

import pytest

from pf.sim.calculators.housing import (
    CashVsLoanIn,
    MortgageIn,
    RentalIn,
    RentVsBuyIn,
    amortize,
    cash_vs_loan,
    payment,
    rent_vs_buy,
    rental,
)


def test_payment_known_values() -> None:
    assert payment(300_000, 0.06, 30) == pytest.approx(1798.65, abs=0.01)
    assert payment(200_000, 0.0, 10) == pytest.approx(200_000 / 120)
    assert payment(0, 0.05, 30) == 0.0


def test_amortization_pays_off_principal_exactly() -> None:
    m = amortize(MortgageIn(300_000, 0.06, 30))
    assert m.months == 360
    assert sum(m.principal_by_year) == pytest.approx(300_000, abs=0.01)
    assert m.total_interest == pytest.approx(1798.65 * 360 - 300_000, abs=5)
    assert m.balance_by_year[0] == 300_000 and m.balance_by_year[-1] == pytest.approx(0, abs=0.01)


def test_extra_payments_shorten_and_save() -> None:
    m = amortize(MortgageIn(300_000, 0.06, 30, extra_monthly=500))
    assert m.months < 300 and m.interest_saved > 100_000
    assert m.total_interest + m.interest_saved == pytest.approx(m.total_interest_no_extra)


def test_cash_vs_loan_breakeven_near_loan_rate() -> None:
    """Investing borrowed money only pays if it earns more than the loan costs."""
    c = cash_vs_loan(CashVsLoanIn(loan=400_000, annual_rate=0.06, years=30, invest_return=0.08))
    assert c.loan_better_at_horizon
    assert 0.055 < c.breakeven_return < 0.065
    worse = cash_vs_loan(CashVsLoanIn(loan=400_000, annual_rate=0.06, years=30, invest_return=0.03))
    assert not worse.loan_better_at_horizon


def test_rent_vs_buy_cheap_rent_favours_renting() -> None:
    cheap_rent = rent_vs_buy(RentVsBuyIn(price=1_000_000, rent=2_500, horizon_years=10))
    assert cheap_rent.rent_net_worth[-1] > cheap_rent.buy_net_worth[-1]
    pricey_rent = rent_vs_buy(RentVsBuyIn(price=400_000, rent=4_000, horizon_years=10))
    assert pricey_rent.breakeven_year is not None and pricey_rent.breakeven_year <= 5
    assert len(pricey_rent.buy_net_worth) == 11
    assert pricey_rent.upfront_cash == pytest.approx(400_000 * 0.23)


def test_rental_simple_case() -> None:
    """All-cash, no expenses, no growth: cap rate = gross yield, IRR = yield."""
    x = RentalIn(
        price=100_000,
        rent=500,
        down_pct=1.0,
        closing_buy_pct=0,
        vacancy_pct=0,
        management_pct=0,
        property_tax_pct=0,
        insurance=0,
        maintenance_pct=0,
        capex_reserve_pct=0,
        appreciation=0,
        rent_growth=0,
        expense_growth=0,
        selling_cost_pct=0,
        hold_years=10,
        tbill_rate=0.04,
    )
    r = rental(x)
    assert r.cap_rate == pytest.approx(0.06)
    assert r.cash_on_cash == pytest.approx(0.06)
    assert r.irr == pytest.approx((1 + 0.005) ** 12 - 1, abs=1e-6)  # 0.5% a month
    assert r.worth_it and r.breakeven_rent == pytest.approx(0, abs=1e-9)


def test_rental_with_real_costs_can_lose_to_tbills() -> None:
    """The 巫师 story: an investment property whose true return is ~0 after every cost."""
    r = rental(
        RentalIn(
            price=800_000,
            rent=3_200,
            tenant_turnover_cost=2_000,
            appreciation=0.01,
            rent_growth=0.01,
        )
    )
    assert r.cash_flow_year1 < 0
    assert r.breakeven_rent > 3_200
    assert not r.worth_it and r.irr < 0.04
