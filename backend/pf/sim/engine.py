"""Vectorised Monte Carlo of household wealth, monthly, in REAL (today's) dollars.

Per month and per path:  holdings grow by the block returns (minus fees)  ->  the month's cash
flow is added (contributions by target weights) or withdrawn (pro rata)  ->  rebalance at the
chosen frequency. With retirement inputs, liquid and pretax retirement holdings are separate:
liquid assets fund cash needs first, then accessible retirement withdrawals include tax.
Ruin means a funding shortfall; locked retirement assets remain invested after a shortfall.
Legacy scenarios without retirement inputs keep the original absorbing-zero ruin behavior.

Alongside wealth it tracks, per path: the month of ruin, the month each lighthouse is first
reached, and the max drawdown / longest underwater spell of the INVESTMENT itself (a unit index
that ignores cash flows - so saving or spending never masquerades as gains or losses)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from pf.sim.returns_model import BLOCKS, Generator, ReturnModel


@dataclass
class Event:
    year: float  # years from now when it first happens
    amount: float  # real $, negative = outflow (buy a car), positive = inflow (inheritance)
    label: str = ""
    every: float | None = None  # repeat every N years
    times: int = 1  # how many occurrences in total


@dataclass
class Phase:
    """A stretch of life with its own monthly money, e.g. school, a gap, a first job in NYC.
    Months are offsets from the current month (0 = this month), both ends inclusive. Inside a
    phase its numbers replace the scenario's steady income/spending (or retirement draw)."""

    start: int
    end: int
    monthly_income: float  # after-tax, real
    monthly_spending: float  # real
    label: str = ""


@dataclass
class Scenario:
    initial: float  # investable wealth today, real $
    weights: dict[str, float]  # target allocation over stocks / bonds / cash, sums to 1
    annual_income: float  # after-tax, real
    annual_spending: float  # real, while working
    work_years: float  # income stops after this many years (0 = already retired)
    horizon_years: int = 50
    income_growth: float = 0.01  # real, per year
    retirement_spending: float | None = None  # default: same as annual_spending
    pension: float = 0.0  # real $/yr from pension_start_year (e.g. Social Security)
    pension_start_year: float = 99.0
    withdrawal: Literal["constant", "percent", "guardrails"] = "constant"
    withdrawal_rate: float = 0.04  # for percent / guardrails / lighthouse 3
    rebalance: Literal["monthly", "annual", "none"] = "annual"
    fee: float = 0.001  # annual drag on invested assets
    expected_return: float | None = None  # for lighthouses 1-2; default: model mean of allocation
    events: list[Event] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)
    annual_retirement_contribution: float = 0.0  # fixed real pretax $/yr, only while working
    retirement_balance: float = 0.0  # pretax retirement portion already INCLUDED in initial
    retirement_withdrawal_tax_rate: float = 0.0  # assumed effective tax on gross withdrawals
    retirement_access_year: float = 0.0  # years until retirement assets can fund cash needs

    def __post_init__(self) -> None:
        for name in ("annual_retirement_contribution", "retirement_access_year"):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0")
        if not np.isfinite(self.retirement_balance) or not (
            0 <= self.retirement_balance <= self.initial
        ):
            raise ValueError("retirement_balance must be finite and between 0 and initial")
        if not np.isfinite(self.retirement_withdrawal_tax_rate) or not (
            0 <= self.retirement_withdrawal_tax_rate <= 1
        ):
            raise ValueError("retirement_withdrawal_tax_rate must be finite and between 0 and 1")

    def w(self) -> np.ndarray:
        v = np.array([self.weights.get(b, 0.0) for b in BLOCKS], dtype=float)
        if v.min() < 0 or abs(v.sum() - 1) > 1e-6:
            raise ValueError(f"weights must be >= 0 and sum to 1, got {self.weights}")
        return v


@dataclass
class SimResult:
    years: list[int]
    percentiles: dict[str, list[float]]  # "p5".."p95" -> gross assets, before future withdrawal tax
    ruin_by_year: list[float]  # cumulative P(ruined by end of year k)
    success: float  # P(not ruined over the horizon)
    terminal_mean: float
    terminal_median: float
    lighthouse_targets: list[float]
    lighthouse_prob: list[float]  # P(reached within horizon)
    lighthouse_median_year: list[float | None]
    mdd_quantiles: dict[str, float]  # investment max drawdown (fraction) p5..p95
    mdd_exceed: dict[str, float]  # P(MDD >= x) for x in 10..60%
    underwater_months_median: float
    paths: int
    expected_return: float
    # raw per-path arrays for downstream analyses (not serialised)
    terminal: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    mdd: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))


PCTS = (5, 25, 50, 75, 95)
MDD_LEVELS = (10, 20, 30, 40, 50, 60)


def event_flows(events: list[Event], months: int) -> np.ndarray:
    flows = np.zeros(months)
    for e in events:
        for k in range(max(1, e.times)):
            t = e.year + k * (e.every or 0)
            m = round(t * 12)
            if 0 <= m < months:
                flows[m] += e.amount
            if not e.every:
                break
    return flows


def phase_flows(phases: list[Phase], months: int) -> tuple[np.ndarray, np.ndarray]:
    """(covered mask, monthly net flow) per month; a later phase wins where two overlap."""
    covered = np.zeros(months, dtype=bool)
    flows = np.zeros(months)
    for p in phases:
        lo, hi = max(0, p.start), min(months - 1, p.end)
        if lo > hi:
            continue
        covered[lo : hi + 1] = True
        flows[lo : hi + 1] = p.monthly_income - p.monthly_spending
    return covered, flows


def working_flow(s: Scenario, t: int) -> float:
    """Net monthly flow of the steady career income, month t (grows in real terms)."""
    return s.annual_income * (1 + s.income_growth) ** (t / 12) / 12 - s.annual_spending / 12


def cash_bridge(s: Scenario, months: int) -> list[dict[str, float | bool]]:
    """The plan's month-by-month cash flow with no market at all: phases, steady income,
    retirement spending and one-off events. What the next months look like in the bank."""
    covered, _ = phase_flows(s.phases, months)
    ev = event_flows(s.events, months)
    work_m = round(s.work_years * 12)
    ret = s.retirement_spending if s.retirement_spending is not None else s.annual_spending
    out: list[dict[str, float | bool]] = []
    for t in range(months):
        phase = next((p for p in reversed(s.phases) if p.start <= t <= p.end), None)
        if phase is not None:
            income, spending = phase.monthly_income, phase.monthly_spending
        elif t < work_m:
            income = s.annual_income * (1 + s.income_growth) ** (t / 12) / 12
            spending = s.annual_spending / 12
        else:
            income, spending = 0.0, ret / 12
        if t >= s.pension_start_year * 12:
            income += s.pension / 12
        out.append(
            {
                "flow": income - spending,
                "income": income,
                "spending": spending,
                "events": float(ev[t]),
                "phase": bool(covered[t]),
            }
        )
    return out


def model_expected_return(gen: Generator, w: np.ndarray) -> float:
    """Annual real expected return of the allocation under the model's history window."""
    mean_m = gen.hist.mean(axis=0) @ w + gen.monthly_shift
    return float((1 + mean_m) ** 12 - 1)


def _withdraw_up_to(holdings: np.ndarray, need: np.ndarray) -> np.ndarray:
    """Take at most each pool's balance, pro rata by asset, and return the unfunded amount."""
    total = holdings.sum(1)
    taken = np.minimum(need, np.maximum(total, 0))
    fraction = np.divide(taken, total, out=np.zeros_like(total), where=total > 0)
    holdings *= 1 - fraction[:, None]
    return need - taken


def simulate(
    s: Scenario,
    model: ReturnModel,
    history: pd.DataFrame,
    paths: int = 10_000,
    seed: int = 7,
) -> SimResult:
    T = s.horizon_years * 12
    w = s.w()
    gen = Generator(model, history, paths, seed)
    exp_r = s.expected_return if s.expected_return is not None else model_expected_return(gen, w)

    work_m = round(s.work_years * 12)
    ret_spend = s.retirement_spending if s.retirement_spending is not None else s.annual_spending
    savings = s.annual_income - s.annual_spending + s.annual_retirement_contribution
    targets = np.array(
        [
            savings / exp_r if exp_r > 0 and savings > 0 else np.inf,
            ret_spend / exp_r if exp_r > 0 else np.inf,
            ret_spend / s.withdrawal_rate if s.withdrawal_rate > 0 else np.inf,
        ]
    )
    ev = event_flows(s.events, T)
    in_phase, phase_flow = phase_flows(s.phases, T)
    fee_m = (1 + s.fee) ** (1 / 12) - 1

    retirement_enabled = any(
        (
            s.annual_retirement_contribution,
            s.retirement_balance,
            s.retirement_withdrawal_tax_rate,
            s.retirement_access_year,
        )
    )
    H = np.repeat(((s.initial - s.retirement_balance) * w)[None, :], paths, axis=0)
    retirement = (
        np.repeat((s.retirement_balance * w)[None, :], paths, axis=0)
        if retirement_enabled
        else None
    )
    alive = np.ones(paths, dtype=bool)
    ruin_month = np.full(paths, -1)
    hit = np.full((3, paths), -1)
    unit = np.ones(paths)  # investment-only index
    peak = np.ones(paths)
    mdd = np.zeros(paths)
    under = np.zeros(paths, dtype=np.int32)
    longest_under = np.zeros(paths, dtype=np.int32)
    wd = np.full(paths, ret_spend)  # guardrails: current annual withdrawal per path

    yearly = np.empty((s.horizon_years + 1, paths))
    yearly[0] = H.sum(1)
    if retirement is not None:
        yearly[0] += retirement.sum(1)
    for t, R in enumerate(gen.months(T)):
        R = R - fee_m
        # investment-only performance (allocation-weighted at target weights)
        port_r = R @ w
        unit *= 1 + port_r
        np.maximum(peak, unit, out=peak)
        dd = 1 - unit / peak
        np.maximum(mdd, dd, out=mdd)
        under = np.where(dd > 1e-9, under + 1, 0)
        np.maximum(longest_under, under, out=longest_under)

        H *= 1 + R
        W = H.sum(1)
        if retirement is not None:
            retirement *= 1 + R
            W += retirement.sum(1)

        # ---- this month's cash flow
        if in_phase[t]:
            flow = np.full(paths, phase_flow[t])
            if t >= s.pension_start_year * 12:
                flow = flow + s.pension / 12
        elif t < work_m:
            flow = np.full(paths, working_flow(s, t))
        else:
            if s.withdrawal == "constant":
                spend = np.full(paths, ret_spend / 12)
            elif s.withdrawal == "percent":
                spend = np.maximum(W, 0) * s.withdrawal_rate / 12
            else:  # guardrails (Guyton-Klinger style), re-evaluated each year of retirement
                if (t - work_m) % 12 == 0 and t > work_m:
                    rate_now = wd / np.maximum(W, 1)
                    wd = np.where(rate_now > s.withdrawal_rate * 1.2, wd * 0.9, wd)
                    wd = np.where(rate_now < s.withdrawal_rate * 0.8, wd * 1.1, wd)
                spend = wd / 12
            pension = s.pension / 12 if t >= s.pension_start_year * 12 else 0.0
            flow = pension - spend
        if t >= s.pension_start_year * 12 and t < work_m and not in_phase[t]:
            flow = flow + s.pension / 12
        flow = flow + ev[t]

        # ---- apply cash needs, keeping account ownership intact
        if retirement is not None:
            if t < work_m:
                retirement += s.annual_retirement_contribution / 12 * w
            H += np.maximum(flow, 0)[:, None] * w
            need = _withdraw_up_to(H, np.maximum(-flow, 0))
            net_fraction = 1 - s.retirement_withdrawal_tax_rate
            if t >= s.retirement_access_year * 12 and net_fraction > 0:
                need = _withdraw_up_to(retirement, need / net_fraction) * net_fraction
            newly_ruined = alive & (need > 0.01)
            # Failure is permanent, but unspent assets, future income, and contributions remain.
            # An unfunded payment is not converted into a fictional loan or a negative holding.
        else:
            # Keep the legacy arithmetic and absorbing ruin behavior for existing scenarios.
            pos = flow >= 0
            H[pos] += flow[pos, None] * w
            neg = ~pos & alive
            if neg.any():
                share = np.where(W[neg, None] > 0, H[neg] / np.maximum(W[neg, None], 1e-12), w)
                H[neg] += flow[neg, None] * share
            W = H.sum(1)
            # Exactly exhausting the balance still funded this month (allow cent residue).
            newly_ruined = alive & (W < -0.01)
            H[alive & (W < 0) & ~newly_ruined] = 0.0
        if newly_ruined.any():
            ruin_month[newly_ruined] = t
            alive &= ~newly_ruined
        if retirement is None:
            H[~alive] = 0.0
        W = H.sum(1)
        if retirement is not None:
            W += retirement.sum(1)

        for k in range(3):
            first = (hit[k] < 0) & alive & (targets[k] <= W)
            hit[k, first] = t

        # ---- rebalance
        if s.rebalance == "monthly" or (s.rebalance == "annual" and t % 12 == 11):
            if retirement is None:
                H = W[:, None] * w
            else:
                H = H.sum(1)[:, None] * w
                retirement = retirement.sum(1)[:, None] * w
        if t % 12 == 11:
            yearly[(t + 1) // 12] = W

    terminal = yearly[-1]
    years = list(range(s.horizon_years + 1))
    ruin_year = np.where(ruin_month >= 0, ruin_month // 12 + 1, s.horizon_years + 10)
    ruin_by_year = [float((ruin_year <= y).mean()) for y in years]
    lh_med: list[float | None] = []
    for k in range(3):
        reached = hit[k] >= 0
        lh_med.append(float(np.median(hit[k][reached]) / 12) if reached.mean() >= 0.5 else None)
    return SimResult(
        years=years,
        percentiles={f"p{p}": np.percentile(yearly, p, axis=1).tolist() for p in PCTS},
        ruin_by_year=ruin_by_year,
        success=1 - ruin_by_year[-1],
        terminal_mean=float(terminal.mean()),
        terminal_median=float(np.median(terminal)),
        lighthouse_targets=[float(x) if np.isfinite(x) else 0.0 for x in targets],
        lighthouse_prob=[float((hit[k] >= 0).mean()) for k in range(3)],
        lighthouse_median_year=lh_med,
        mdd_quantiles={f"p{p}": float(np.percentile(mdd, p)) for p in PCTS},
        mdd_exceed={f"{x}": float((mdd >= x / 100).mean()) for x in MDD_LEVELS},
        underwater_months_median=float(np.median(longest_under)),
        paths=paths,
        expected_return=exp_r,
        terminal=terminal,
        mdd=mdd,
    )
