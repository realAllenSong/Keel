"""Backtest lab and decision calculators."""

from __future__ import annotations

from dataclasses import MISSING, asdict, fields
from typing import Any, get_type_hints

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, create_model

from pf.i18n import tr
from pf.market.providers.prices import LONG_US, monthly_prices
from pf.sim.backtest import LABELS, STRATEGIES, Params, irr_monthly, max_drawdown, rolling, single
from pf.sim.calculators import housing as h

router = APIRouter(prefix="/api", tags=["tools"])


# ---------------------------------------------------------------- backtest
class BacktestIn(BaseModel):
    ticker: str = "QQQ"
    monthly: float = Field(default=1000, gt=0)
    windows: list[int] = Field(default=[10, 20])
    dip_threshold: float = Field(default=0.10, gt=0, lt=0.9)
    ma_months: int = Field(default=10, ge=2, le=24)
    cash_rate: float = 0.0
    start: str | None = None  # restrict history
    detail_start: str | None = None  # one window to draw in detail
    detail_years: int = 10


class StratSummary(BaseModel):
    multiple_p5: float
    multiple_p50: float
    multiple_p95: float
    irr_p50: float
    irr_p5: float
    mdd_p50: float
    mdd_max: float


class WindowOut(BaseModel):
    years: int
    starts: list[str]
    multiple: dict[str, list[float]]
    summary: dict[str, StratSummary]
    dca_win_rate: dict[str, float]
    worst_start: dict[str, str]


class DetailOut(BaseModel):
    start: str
    months: list[str]
    contributed: list[float]
    value: dict[str, list[float]]
    irr: dict[str, float]
    mdd: dict[str, float]


class BacktestOut(BaseModel):
    ticker: str
    first: str
    last: str
    labels: dict[str, str]
    windows: list[WindowOut]
    detail: DetailOut | None


@router.get("/backtest/tickers")
def tickers() -> dict[str, list[str]]:
    return {"suggested": ["QQQ", "SPY", "VTI", "VXUS", "BND", "BTC-USD", LONG_US]}


@router.post("/backtest/run", response_model=BacktestOut)
def backtest(body: BacktestIn) -> BacktestOut:
    try:
        px = monthly_prices(body.ticker)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # network / unknown ticker
        raise HTTPException(502, tr("拿不到 {t} 的价格：{e}", t=body.ticker, e=e)) from e
    if body.start:
        px = px.loc[body.start :]  # type: ignore[misc]
    if len(px) < 24:
        raise HTTPException(400, tr("历史太短（不足 24 个月）"))
    p = Params(body.monthly, body.dip_threshold, body.ma_months, body.cash_rate)
    wins = []
    for yrs in sorted({y for y in body.windows if 1 <= y <= 60}):
        if yrs * 12 > len(px):
            continue
        w = rolling(px, yrs, p)
        wins.append(
            WindowOut(
                years=w.years,
                starts=w.starts,
                multiple=w.multiple,
                summary={k: StratSummary(**v) for k, v in w.summary.items()},
                dca_win_rate=w.dca_win_rate,
                worst_start=w.worst_start,
            )
        )
    detail = None
    ds = body.detail_start or str(px.index[0])
    if ds in set(px.index.astype(str)):
        paths = single(px, ds, body.detail_years, p)
        n = len(paths["dca"].value)
        all_months = [str(m) for m in px.index]
        i0 = all_months.index(ds)
        months = all_months[i0 : i0 + n]
        detail = DetailOut(
            start=ds,
            months=months,
            contributed=paths["dca"].contributed.tolist(),
            value={k: v.value.tolist() for k, v in paths.items()},
            irr={k: float(np.nan_to_num(irr_monthly(v.cashflows))) for k, v in paths.items()},
            mdd={k: max_drawdown(v.value) for k, v in paths.items()},
        )
    return BacktestOut(
        ticker=body.ticker.upper(),
        first=str(px.index.min()),
        last=str(px.index.max()),
        labels={k: tr(LABELS[k]) for k in STRATEGIES},
        windows=wins,
        detail=detail,
    )


# ---------------------------------------------------------------- calculators
def _model_from(dc: type, name: str) -> type[BaseModel]:
    """Pydantic input model generated from a calculator dataclass (single source of defaults)."""
    hints = get_type_hints(dc)  # real types, even under `from __future__ import annotations`
    spec: dict[str, Any] = {}
    for f in fields(dc):
        if f.default is not MISSING:
            default: Any = f.default
        elif f.default_factory is not MISSING:
            default = Field(default_factory=f.default_factory)
        else:
            default = ...
        spec[f.name] = (hints[f.name], default)
    return create_model(name, **spec)


MortgageModel = _model_from(h.MortgageIn, "MortgageModel")
CashVsLoanModel = _model_from(h.CashVsLoanIn, "CashVsLoanModel")
RentVsBuyModel = _model_from(h.RentVsBuyIn, "RentVsBuyModel")
RentalModel = _model_from(h.RentalIn, "RentalModel")


class MortgageResult(BaseModel):
    payment: float
    months: int
    total_interest: float
    total_interest_no_extra: float
    interest_saved: float
    balance_by_year: list[float]
    interest_by_year: list[float]
    principal_by_year: list[float]


class CashVsLoanResult(BaseModel):
    payment: float
    advantage_by_year: list[float]
    loan_better_at_horizon: bool
    breakeven_return: float


class RentVsBuyResult(BaseModel):
    buy_net_worth: list[float]
    rent_net_worth: list[float]
    breakeven_year: int | None
    monthly_cost_buy_year1: float
    monthly_cost_rent_year1: float
    upfront_cash: float


class RentalResult(BaseModel):
    cash_invested: float
    noi_year1: float
    cap_rate: float
    debt_service_year1: float
    cash_flow_year1: float
    monthly_cash_flow_year1: float
    cash_on_cash: float
    breakeven_rent: float
    irr: float
    equity_multiple: float
    tbill_equivalent: float
    sale_proceeds: float
    cash_flows_by_year: list[float]
    worth_it: bool


@router.post("/calc/mortgage", response_model=MortgageResult)
def calc_mortgage(body: MortgageModel) -> MortgageResult:  # type: ignore[valid-type]
    return MortgageResult(**asdict(h.amortize(h.MortgageIn(**body.model_dump()))))  # type: ignore[attr-defined]


@router.post("/calc/cash-vs-loan", response_model=CashVsLoanResult)
def calc_cash_vs_loan(body: CashVsLoanModel) -> CashVsLoanResult:  # type: ignore[valid-type]
    return CashVsLoanResult(**asdict(h.cash_vs_loan(h.CashVsLoanIn(**body.model_dump()))))  # type: ignore[attr-defined]


@router.post("/calc/rent-vs-buy", response_model=RentVsBuyResult)
def calc_rent_vs_buy(body: RentVsBuyModel) -> RentVsBuyResult:  # type: ignore[valid-type]
    return RentVsBuyResult(**asdict(h.rent_vs_buy(h.RentVsBuyIn(**body.model_dump()))))  # type: ignore[attr-defined]


@router.post("/calc/rental", response_model=RentalResult)
def calc_rental(body: RentalModel) -> RentalResult:  # type: ignore[valid-type]
    r = h.rental(h.RentalIn(**body.model_dump()))  # type: ignore[attr-defined]
    d = asdict(r)
    d["irr"] = float(np.nan_to_num(d["irr"], nan=-1.0))
    d["breakeven_rent"] = float(min(d["breakeven_rent"], 1e12))
    return RentalResult(**d)
