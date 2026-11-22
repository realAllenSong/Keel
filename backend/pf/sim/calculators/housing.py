"""Decision calculators (视频第 4 条：重大决策先做数学模拟). Pure functions, nominal dollars,
monthly steps. Taxes (mortgage-interest deduction, capital gains, depreciation) are NOT modelled -
the UI says so - because they depend on your jurisdiction and bracket."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pf.sim.backtest import irr_monthly


def _m(rate: float) -> float:
    return (1 + rate) ** (1 / 12) - 1


def payment(principal: float, annual_rate: float, years: float) -> float:
    """Level monthly payment (US convention: nominal annual rate / 12)."""
    n = round(years * 12)
    r = annual_rate / 12
    if principal <= 0 or n <= 0:
        return 0.0
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


# ---------------------------------------------------------------- mortgage
@dataclass
class MortgageIn:
    principal: float
    annual_rate: float
    years: float = 30
    extra_monthly: float = 0.0


@dataclass
class MortgageOut:
    payment: float
    months: int  # until paid off (with extra payments)
    total_interest: float
    total_interest_no_extra: float
    interest_saved: float
    balance_by_year: list[float]
    interest_by_year: list[float]
    principal_by_year: list[float]


def amortize(p: MortgageIn) -> MortgageOut:
    pay = payment(p.principal, p.annual_rate, p.years)
    r = p.annual_rate / 12
    n = round(p.years * 12)

    def run(extra: float) -> tuple[int, float, list[float], list[float], list[float]]:
        bal, total_i, month = p.principal, 0.0, 0
        bal_y, int_y, prin_y = [p.principal], [], []
        yi = yp = 0.0
        while bal > 0.005 and month < n:
            i = bal * r
            princ = min(bal, pay - i + extra)
            bal -= princ
            total_i += i
            yi += i
            yp += princ
            month += 1
            if month % 12 == 0 or bal <= 0.005:
                bal_y.append(max(bal, 0.0))
                int_y.append(yi)
                prin_y.append(yp)
                yi = yp = 0.0
        return month, total_i, bal_y, int_y, prin_y

    base = run(0.0)
    with_extra = run(p.extra_monthly)
    return MortgageOut(
        payment=pay,
        months=with_extra[0],
        total_interest=with_extra[1],
        total_interest_no_extra=base[1],
        interest_saved=base[1] - with_extra[1],
        balance_by_year=with_extra[2],
        interest_by_year=with_extra[3],
        principal_by_year=with_extra[4],
    )


@dataclass
class CashVsLoanIn:
    loan: float  # amount you could borrow instead of paying cash
    annual_rate: float
    years: float = 30
    invest_return: float = 0.07  # expected return on the money kept invested
    horizon_years: int = 30


@dataclass
class CashVsLoanOut:
    payment: float
    # net worth difference (loan - cash) at each year end; > 0 means borrowing came out ahead
    advantage_by_year: list[float]
    loan_better_at_horizon: bool
    breakeven_return: float  # investment return at which both options tie at the horizon


def cash_vs_loan(c: CashVsLoanIn) -> CashVsLoanOut:
    """Borrow and keep the cash invested, vs pay cash and invest the would-be mortgage payment
    every month instead. Same money out of pocket each month in both cases."""

    def advantage(ret: float) -> list[float]:
        pay = payment(c.loan, c.annual_rate, c.years)
        r_inv, r_loan = _m(ret), c.annual_rate / 12
        n_loan = round(c.years * 12)
        inv_loan, bal = c.loan, c.loan  # option B: lump kept invested, loan outstanding
        inv_cash = 0.0  # option A: pays cash, invests the payment it no longer owes
        out = [0.0]
        for t in range(c.horizon_years * 12):
            inv_loan *= 1 + r_inv
            inv_cash *= 1 + r_inv
            if t < n_loan and bal > 0:
                interest = bal * r_loan
                bal = max(0.0, bal - (pay - interest))
                inv_cash += pay
            if t % 12 == 11:
                out.append((inv_loan - bal) - inv_cash)
        return out

    adv = advantage(c.invest_return)
    lo, hi = -0.5, 1.0
    for _ in range(80):  # bisection on the return that makes the horizon advantage zero
        mid = (lo + hi) / 2
        if advantage(mid)[-1] > 0:
            hi = mid
        else:
            lo = mid
    return CashVsLoanOut(payment(c.loan, c.annual_rate, c.years), adv, adv[-1] > 0, (lo + hi) / 2)


# ---------------------------------------------------------------- rent vs buy
@dataclass
class RentVsBuyIn:
    price: float
    rent: float  # monthly
    down_pct: float = 0.20
    annual_rate: float = 0.065
    years: float = 30
    closing_buy_pct: float = 0.03
    selling_cost_pct: float = 0.06
    property_tax_pct: float = 0.012  # of home value per year
    insurance: float = 1800.0  # per year
    maintenance_pct: float = 0.01  # of home value per year
    hoa: float = 0.0  # monthly
    appreciation: float = 0.03
    rent_growth: float = 0.03
    renters_insurance: float = 240.0  # per year
    invest_return: float = 0.07
    horizon_years: int = 30


@dataclass
class RentVsBuyOut:
    buy_net_worth: list[float]
    rent_net_worth: list[float]
    breakeven_year: int | None  # first year buying is ahead and stays ahead
    monthly_cost_buy_year1: float
    monthly_cost_rent_year1: float
    upfront_cash: float


def rent_vs_buy(x: RentVsBuyIn) -> RentVsBuyOut:
    down = x.price * x.down_pct
    closing = x.price * x.closing_buy_pct
    loan = x.price - down
    pay = payment(loan, x.annual_rate, x.years)
    r_loan, r_inv = x.annual_rate / 12, _m(x.invest_return)
    home, bal = x.price, loan
    rent = x.rent
    buyer_port = 0.0
    renter_port = down + closing  # renter invests what the buyer spent up front
    buy_nw = [x.price * (1 - x.selling_cost_pct) - loan]  # year 0 if sold immediately
    rent_nw = [renter_port]
    first_buy = first_rent = 0.0
    n_loan = round(x.years * 12)
    for t in range(x.horizon_years * 12):
        if t > 0 and t % 12 == 0:
            rent *= 1 + x.rent_growth
        mortgage = 0.0
        if t < n_loan and bal > 0:
            interest = bal * r_loan
            princ = min(bal, pay - interest)
            bal -= princ
            mortgage = interest + princ
        owner = (
            mortgage
            + home * (x.property_tax_pct + x.maintenance_pct) / 12
            + x.insurance / 12
            + x.hoa
        )
        renter = rent + x.renters_insurance / 12
        if t == 0:
            first_buy, first_rent = owner, renter
        buyer_port *= 1 + r_inv
        renter_port *= 1 + r_inv
        diff = owner - renter
        if diff > 0:
            renter_port += diff  # renting is cheaper this month: renter invests the difference
        else:
            buyer_port += -diff
        home *= 1 + _m(x.appreciation)
        if t % 12 == 11:
            buy_nw.append(home * (1 - x.selling_cost_pct) - bal + buyer_port)
            rent_nw.append(renter_port)
    ahead = [b >= r for b, r in zip(buy_nw, rent_nw, strict=True)]
    breakeven = next((y for y in range(len(ahead)) if all(ahead[y:])), None)
    return RentVsBuyOut(buy_nw, rent_nw, breakeven, first_buy, first_rent, down + closing)


# ---------------------------------------------------------------- rental property
@dataclass
class RentalIn:
    price: float
    rent: float  # monthly market rent
    down_pct: float = 0.25
    annual_rate: float = 0.07
    years: float = 30
    closing_buy_pct: float = 0.03
    vacancy_pct: float = 0.05
    management_pct: float = 0.08  # of collected rent
    property_tax_pct: float = 0.012
    insurance: float = 2000.0
    maintenance_pct: float = 0.01  # of price per year
    capex_reserve_pct: float = 0.005  # roof, HVAC... of price per year
    hoa: float = 0.0
    tenant_turnover_cost: float = 0.0  # per year (listing, cleaning, leasing fee averaged)
    appreciation: float = 0.03
    rent_growth: float = 0.03
    expense_growth: float = 0.03
    selling_cost_pct: float = 0.06
    hold_years: int = 10
    tbill_rate: float = 0.04


@dataclass
class RentalOut:
    cash_invested: float
    noi_year1: float
    cap_rate: float
    debt_service_year1: float
    cash_flow_year1: float
    monthly_cash_flow_year1: float
    cash_on_cash: float
    breakeven_rent: float  # monthly rent at which year-1 cash flow is zero
    irr: float  # over the holding period incl. sale
    equity_multiple: float
    tbill_equivalent: float  # what the same cash would be at the T-bill rate
    sale_proceeds: float
    cash_flows_by_year: list[float] = field(default_factory=list)
    worth_it: bool = False


def _year1_cash_flow(x: RentalIn, rent: float) -> tuple[float, float, float]:
    collected = rent * 12 * (1 - x.vacancy_pct)
    opex = (
        collected * x.management_pct
        + x.price * (x.property_tax_pct + x.maintenance_pct + x.capex_reserve_pct)
        + x.insurance
        + x.hoa * 12
        + x.tenant_turnover_cost
    )
    noi = collected - opex
    debt = payment(x.price * (1 - x.down_pct), x.annual_rate, x.years) * 12
    return noi, debt, noi - debt


def rental(x: RentalIn) -> RentalOut:
    cash_in = x.price * (x.down_pct + x.closing_buy_pct)
    noi1, debt1, cf1 = _year1_cash_flow(x, x.rent)
    # break-even rent: cash flow is linear in rent
    _, _, cf0 = _year1_cash_flow(x, 0.0)
    slope = (cf1 - cf0) / x.rent if x.rent else 0.0
    breakeven = -cf0 / slope if slope else float("inf")

    loan = x.price * (1 - x.down_pct)
    pay = payment(loan, x.annual_rate, x.years)
    r_loan = x.annual_rate / 12
    bal = loan
    rent, expense_scale, value = x.rent, 1.0, x.price
    monthly_flows = [-cash_in]
    yearly: list[float] = []
    yr = 0.0
    for t in range(x.hold_years * 12):
        if t > 0 and t % 12 == 0:
            rent *= 1 + x.rent_growth
            expense_scale *= 1 + x.expense_growth
        collected = rent * (1 - x.vacancy_pct)
        opex = (
            collected * x.management_pct
            + expense_scale
            * (
                x.price * (x.property_tax_pct + x.maintenance_pct + x.capex_reserve_pct)
                + x.insurance
                + x.tenant_turnover_cost
            )
            / 12
            + x.hoa * expense_scale
        )
        interest = bal * r_loan
        princ = min(bal, pay - interest) if bal > 0 else 0.0
        bal -= princ
        cf = collected - opex - (interest + princ if princ > 0 or interest > 0 else 0.0)
        value *= 1 + _m(x.appreciation)
        monthly_flows.append(cf)
        yr += cf
        if t % 12 == 11:
            yearly.append(yr)
            yr = 0.0
    sale = value * (1 - x.selling_cost_pct) - bal
    monthly_flows[-1] += sale
    flows = np.array(monthly_flows)
    irr = irr_monthly(flows)
    total_back = float(flows[1:].sum())
    tbill = cash_in * (1 + x.tbill_rate) ** x.hold_years
    return RentalOut(
        cash_invested=cash_in,
        noi_year1=noi1,
        cap_rate=noi1 / x.price,
        debt_service_year1=debt1,
        cash_flow_year1=cf1,
        monthly_cash_flow_year1=cf1 / 12,
        cash_on_cash=cf1 / cash_in,
        breakeven_rent=breakeven,
        irr=irr,
        equity_multiple=total_back / cash_in,
        tbill_equivalent=tbill,
        sale_proceeds=sale,
        cash_flows_by_year=yearly,
        worth_it=bool(irr > x.tbill_rate),
    )
