"""Analyses built on the engine. All comparisons reuse ONE seed so every variant faces identical
markets (common random numbers): differences come from the decision, not from luck."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from pf.i18n import tr
from pf.sim.engine import Scenario, simulate
from pf.sim.returns_model import BLOCKS, ReturnModel


# ---------------------------------------------------------------- sensitivity (tornado)
@dataclass
class Sensitivity:
    variable: str
    label: str
    low_label: str
    high_label: str
    success_low: float
    success_high: float
    median_low: float
    median_high: float


def _shift_stocks(s: Scenario, dp: float) -> Scenario:
    w = dict(s.weights)
    stocks = min(1.0, max(0.0, w.get("stocks", 0.0) + dp))
    moved = stocks - w.get("stocks", 0.0)
    w["stocks"] = stocks
    # take from / give to bonds first, then cash
    for b in ("bonds", "cash"):
        give = min(w.get(b, 0.0), moved) if moved > 0 else moved
        w[b] = w.get(b, 0.0) - give
        moved -= give
        if abs(moved) < 1e-12:
            break
    return replace(s, weights=w)


def sensitivity(
    s: Scenario, model: ReturnModel, history: pd.DataFrame, paths: int = 3000, seed: int = 7
) -> tuple[float, float, list[Sensitivity]]:
    def run(sc: Scenario, m: ReturnModel = model) -> tuple[float, float]:
        r = simulate(sc, m, history, paths=paths, seed=seed)
        return r.success, r.terminal_median

    base_success, base_median = run(s)
    variants: list[
        tuple[str, str, str, str, tuple[Scenario, ReturnModel], tuple[Scenario, ReturnModel]]
    ] = [
        (
            "spending",
            tr("年开销 ±10%"),
            "−10%",
            "+10%",
            (
                replace(
                    s,
                    annual_spending=s.annual_spending * 0.9,
                    retirement_spending=_scale(s.retirement_spending, 0.9),
                ),
                model,
            ),
            (
                replace(
                    s,
                    annual_spending=s.annual_spending * 1.1,
                    retirement_spending=_scale(s.retirement_spending, 1.1),
                ),
                model,
            ),
        ),
        (
            "income",
            tr("年收入 ±10%"),
            "−10%",
            "+10%",
            (replace(s, annual_income=s.annual_income * 0.9), model),
            (replace(s, annual_income=s.annual_income * 1.1), model),
        ),
        (
            "work_years",
            tr("工作年限 ±2 年"),
            tr("早 2 年退休"),
            tr("晚 2 年退休"),
            (replace(s, work_years=max(0.0, s.work_years - 2)), model),
            (replace(s, work_years=s.work_years + 2), model),
        ),
        (
            "stocks",
            tr("股票比例 ±10pp"),
            "−10pp",
            "+10pp",
            (_shift_stocks(s, -0.10), model),
            (_shift_stocks(s, 0.10), model),
        ),
        (
            "returns",
            tr("市场实际收益 ±1%/年"),
            "−1%",
            "+1%",
            (s, replace(model, shift=model.shift - 0.01)),
            (s, replace(model, shift=model.shift + 0.01)),
        ),
        (
            "initial",
            tr("当前资产 ±20%"),
            "−20%",
            "+20%",
            (
                replace(s, initial=s.initial * 0.8, retirement_balance=s.retirement_balance * 0.8),
                model,
            ),
            (
                replace(s, initial=s.initial * 1.2, retirement_balance=s.retirement_balance * 1.2),
                model,
            ),
        ),
    ]
    out = []
    for var, label, lo_l, hi_l, (s_lo, m_lo), (s_hi, m_hi) in variants:
        lo, hi = run(s_lo, m_lo), run(s_hi, m_hi)
        out.append(Sensitivity(var, label, lo_l, hi_l, lo[0], hi[0], lo[1], hi[1]))
    out.sort(
        key=lambda x: (
            -abs(x.success_high - x.success_low)
            - abs(x.median_high - x.median_low) / max(base_median, 1)
        )
    )
    return base_success, base_median, out


def _scale(v: float | None, k: float) -> float | None:
    return None if v is None else v * k


# ---------------------------------------------------------------- earliest retirement
@dataclass
class RetirementCurve:
    years: list[int]
    success: list[float]
    earliest: int | None  # first year with success >= target, None if never within range
    target: float


def retirement_curve(
    s: Scenario,
    model: ReturnModel,
    history: pd.DataFrame,
    target: float = 0.95,
    max_years: int = 40,
    step: int = 1,
    paths: int = 1500,
    seed: int = 7,
) -> RetirementCurve:
    years = list(range(0, max_years + 1, step))
    succ = [
        simulate(replace(s, work_years=float(k)), model, history, paths=paths, seed=seed).success
        for k in years
    ]
    earliest = next((k for k, v in zip(years, succ, strict=True) if v >= target), None)
    return RetirementCurve(years, succ, earliest, target)


def earliest_retirement(
    s: Scenario,
    model: ReturnModel,
    history: pd.DataFrame,
    target: float = 0.95,
    max_years: int = 40,
    paths: int = 1500,
    seed: int = 7,
) -> RetirementCurve:
    """The earliest year in `retirement_curve`, found by bisection: working longer never lowers
    success (more saving, a shorter retirement, same draws), so about log2(max_years) simulations
    do instead of one per year. Only the years it evaluated come back, in order."""
    seen: dict[int, float] = {}

    def ok(k: int) -> bool:
        if k not in seen:
            seen[k] = simulate(
                replace(s, work_years=float(k)), model, history, paths=paths, seed=seed
            ).success
        return seen[k] >= target

    earliest: int | None = None
    if ok(max_years):
        lo, hi = 0, max_years  # invariant: hi meets the target
        if ok(lo):
            hi = lo
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ok(mid):
                hi = mid
            else:
                lo = mid
        earliest = hi
    years = sorted(seen)
    return RetirementCurve(years, [seen[k] for k in years], earliest, target)


# ---------------------------------------------------------------- historical worst starts
WORST_STARTS = ("1929-09", "1937-03", "1966-01", "1973-01", "2000-01", "2008-01")


@dataclass
class Replay:
    start: str
    years: list[int]
    wealth: list[float]
    ruined_year: int | None
    complete: bool  # False if history ends before the horizon


def replays(
    s: Scenario, history: pd.DataFrame, starts: tuple[str, ...] = WORST_STARTS
) -> list[Replay]:
    out = []
    last = history.index.max()
    for st in starts:
        p = pd.Period(st, freq="M")
        if p not in history.index:
            continue
        avail_years = int((last - p).n // 12)  # type: ignore[union-attr]
        yrs = min(s.horizon_years, avail_years)
        if yrs < 1:
            continue
        sc = replace(s, horizon_years=yrs)
        r = simulate(
            sc,
            ReturnModel("replay", window_start=str(history.index.min()), replay_start=st),
            history,
            paths=1,
        )
        ruined = next((y for y, v in enumerate(r.ruin_by_year) if v > 0), None)
        out.append(Replay(st, r.years, r.percentiles["p50"], ruined, yrs == s.horizon_years))
    return out


# ---------------------------------------------------------------- optimal allocation
@dataclass
class AllocationPoint:
    weights: dict[str, float]
    success: float
    e_log: float  # E[log terminal wealth] (ruin floored)
    mean: float
    median: float
    p5: float
    mdd_p50: float
    mdd_exceed: float  # P(MDD >= max_drawdown)
    feasible: bool


@dataclass
class Optimization:
    objective: str
    max_ruin: float
    max_drawdown: float
    max_drawdown_prob: float
    points: list[AllocationPoint]
    best: int | None
    current: AllocationPoint


def allocation_grid(step: float) -> list[dict[str, float]]:
    n = round(1 / step)
    out = []
    for i in range(n + 1):
        for j in range(n + 1 - i):
            k = n - i - j
            out.append({"stocks": i / n, "bonds": j / n, "cash": k / n})
    return out


def optimize(
    s: Scenario,
    model: ReturnModel,
    history: pd.DataFrame,
    objective: str = "log",  # log (growth-optimal / Kelly) | mean | median
    max_ruin: float = 0.05,
    max_drawdown: float = 0.4,
    max_drawdown_prob: float = 0.5,
    step: float = 0.1,
    paths: int = 2000,
    seed: int = 7,
) -> Optimization:
    floor = max(1_000.0, 0.01 * s.initial)

    def evaluate(w: dict[str, float]) -> AllocationPoint:
        r = simulate(replace(s, weights=w), model, history, paths=paths, seed=seed)
        term = r.terminal
        exceed = float((r.mdd >= max_drawdown).mean())
        return AllocationPoint(
            weights={b: round(w.get(b, 0.0), 4) for b in BLOCKS},
            success=r.success,
            e_log=float(np.mean(np.log(np.maximum(term, 0) + floor))),
            mean=float(term.mean()),
            median=float(np.median(term)),
            p5=float(np.percentile(term, 5)),
            mdd_p50=float(np.median(r.mdd)),
            mdd_exceed=exceed,
            feasible=(1 - r.success) <= max_ruin + 1e-12 and exceed <= max_drawdown_prob + 1e-12,
        )

    points = [evaluate(w) for w in allocation_grid(step)]
    key = {"log": lambda p: p.e_log, "mean": lambda p: p.mean, "median": lambda p: p.median}[
        objective
    ]
    feasible = [i for i, p in enumerate(points) if p.feasible]
    best = max(feasible, key=lambda i: key(points[i])) if feasible else None
    return Optimization(
        objective,
        max_ruin,
        max_drawdown,
        max_drawdown_prob,
        points,
        best,
        evaluate(copy.copy(s.weights)),
    )
