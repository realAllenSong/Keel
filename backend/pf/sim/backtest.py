"""Backtest lab: how would investing a fixed monthly amount have worked, with different rules,
over EVERY rolling window of history?

Strategies (same money, same months; prices are month-end total-return prices):
  dca      invest the monthly amount every month
  lump     invest the whole window's money (amount x months) at the start
  dip      keep cash; invest ALL accumulated cash whenever price <= (1 - threshold) x all-time high
  ma       timing: hold the asset while price > its N-month moving average, else hold cash
  smart    "smart DCA" with the same money: invest less near the high and more in drawdowns
           (0.5x the monthly amount within 5% of the all-time high, 1x at 5-15% down, 2x at
           15-30%, 3x beyond), from whatever cash has been held back

Cash earns `cash_rate` (annual). No taxes or trading costs."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

STRATEGIES = ("dca", "lump", "dip", "ma", "smart")
LABELS = {
    "dca": "每月定投",
    "lump": "一次性投入",
    "dip": "等跌了再买",
    "ma": "均线择时",
    "smart": "回撤加码定投",
}
SMART_STEPS = ((0.05, 0.5), (0.15, 1.0), (0.30, 2.0))  # (drawdown below, x monthly); beyond: 3x
SMART_MAX = 3.0


def smart_multiple(drawdown: np.ndarray) -> np.ndarray:
    """How many monthly amounts "smart DCA" invests at a given drawdown from the all-time high."""
    return np.select(
        [drawdown < lim for lim, _ in SMART_STEPS], [m for _, m in SMART_STEPS], SMART_MAX
    )


@dataclass
class Params:
    monthly: float = 1000.0
    dip_threshold: float = 0.10  # deploy cash after a >= 10% drop from the all-time high
    ma_months: int = 10
    cash_rate: float = 0.0


@dataclass
class Path:
    value: np.ndarray  # portfolio value at each month end
    contributed: np.ndarray  # cumulative money put in
    cashflows: np.ndarray  # investor -> portfolio, for IRR (negative = invested)


def run_strategy(kind: str, px: np.ndarray, ath: np.ndarray, sma: np.ndarray, p: Params) -> Path:
    n = len(px)
    cash_m = (1 + p.cash_rate) ** (1 / 12) - 1
    units = 0.0
    cash = 0.0
    value = np.empty(n)
    flows = np.zeros(n)
    contributed = np.empty(n)
    total = 0.0
    for t in range(n):
        cash *= 1 + cash_m
        # lump: the whole window's money on day one; everyone else: the monthly amount
        amt = (p.monthly * n if t == 0 else 0.0) if kind == "lump" else p.monthly
        total += amt
        flows[t] = -amt
        cash += amt
        if kind in ("dca", "lump"):
            units += cash / px[t]
            cash = 0.0
        elif kind == "dip":
            if px[t] <= (1 - p.dip_threshold) * ath[t] and cash > 0:
                units += cash / px[t]
                cash = 0.0
        elif kind == "ma":
            if np.isnan(sma[t]) or px[t] > sma[t]:
                units += cash / px[t]
                cash = 0.0
            else:
                cash += units * px[t]
                units = 0.0
        elif kind == "smart":
            buy = min(cash, p.monthly * float(smart_multiple(np.array(1 - px[t] / ath[t]))))
            units += buy / px[t]
            cash -= buy
        value[t] = units * px[t] + cash
        contributed[t] = total
    # Final value is measured at the end of the last month - the same instant as the last
    # contribution - so it belongs in the same slot (an extra slot would discount it a month).
    flows[n - 1] += value[-1]
    return Path(value, contributed, flows)


def irr_monthly(flows: np.ndarray) -> float:
    """Money-weighted return, annualised. Robust bisection on the monthly rate."""

    k = np.arange(len(flows))

    def npv(r: float) -> float:
        return float(np.sum(flows / (1 + r) ** k))

    # Monthly-rate bracket [-50%, +100%] = annual -99.98% .. +409,500%: wide enough for any
    # realistic window while keeping (1 + r)**k finite over decades.
    lo, hi = -0.5, 1.0
    if npv(lo) * npv(hi) > 0:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (1 + (lo + hi) / 2) ** 12 - 1


def max_drawdown(v: np.ndarray) -> float:
    peak = np.maximum.accumulate(v)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, 1 - v / peak, 0.0)
    return float(dd.max())


# ---------------------------------------------------------------- vectorised over windows
def run_windows(
    kind: str, PX: np.ndarray, ATH: np.ndarray, SMA: np.ndarray, p: Params
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Same rules as `run_strategy`, for all windows at once (rows = windows, cols = months).
    Returns (value W x n, contributed n, cashflows W x n)."""
    W, n = PX.shape
    cash_m = (1 + p.cash_rate) ** (1 / 12) - 1
    units = np.zeros(W)
    cash = np.zeros(W)
    value = np.empty((W, n))
    flows = np.zeros((W, n))
    contributed = np.empty(n)
    total = 0.0
    for t in range(n):
        cash *= 1 + cash_m
        amt = (p.monthly * n if t == 0 else 0.0) if kind == "lump" else p.monthly
        total += amt
        flows[:, t] = -amt
        cash += amt
        px = PX[:, t]
        if kind in ("dca", "lump"):
            units += cash / px
            cash[:] = 0.0
        elif kind == "dip":
            buy = (px <= (1 - p.dip_threshold) * ATH[:, t]) & (cash > 0)
            units = np.where(buy, units + cash / px, units)
            cash = np.where(buy, 0.0, cash)
        elif kind == "ma":
            hold = np.isnan(SMA[:, t]) | (px > SMA[:, t])
            new_units = np.where(hold, units + cash / px, 0.0)
            cash = np.where(hold, 0.0, cash + units * px)
            units = new_units
        elif kind == "smart":
            buy = np.minimum(cash, p.monthly * smart_multiple(1 - px / ATH[:, t]))
            units += buy / px
            cash = cash - buy
        value[:, t] = units * px + cash
        contributed[t] = total
    flows[:, n - 1] += value[:, -1]
    return value, contributed, flows


def irr_rows(flows: np.ndarray, iters: int = 60) -> np.ndarray:
    """Annualised money-weighted return for every row of a cash-flow matrix (vector bisection)."""
    W, n = flows.shape
    k = np.arange(n)
    lo, hi = np.full(W, -0.5), np.full(W, 1.0)

    def npv(r: np.ndarray) -> np.ndarray:
        return (flows / (1 + r[:, None]) ** k).sum(axis=1)

    f_lo = npv(lo)
    valid = f_lo * npv(hi) <= 0
    for _ in range(iters):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        left = f_lo * f_mid <= 0
        hi = np.where(left, mid, hi)
        lo = np.where(left, lo, mid)
        f_lo = np.where(left, f_lo, f_mid)
    out = (1 + (lo + hi) / 2) ** 12 - 1
    out[~valid] = np.nan
    return out


def mdd_rows(value: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(value, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, 1 - value / peak, 0.0)
    return dd.max(axis=1)


@dataclass
class WindowStats:
    years: int
    starts: list[str]
    multiple: dict[str, list[float]]  # terminal value / money contributed, per start
    irr: dict[str, list[float]]
    mdd: dict[str, list[float]]
    summary: dict[str, dict[str, float]] = field(default_factory=dict)
    dca_win_rate: dict[str, float] = field(default_factory=dict)  # P(dca terminal >= other)
    worst_start: dict[str, str] = field(default_factory=dict)


def _indicators(px: pd.Series, ma_months: int) -> tuple[np.ndarray, np.ndarray]:
    arr = px.to_numpy(dtype=float)
    ath = np.maximum.accumulate(arr)
    sma = np.asarray(pd.Series(arr).rolling(ma_months).mean(), dtype=float)
    return ath, sma


def rolling(px: pd.Series, years: int, p: Params) -> WindowStats:
    n = years * 12
    arr = px.to_numpy(dtype=float)
    ath, sma = _indicators(px, p.ma_months)
    starts = list(range(0, len(arr) - n + 1))
    ws = WindowStats(
        years,
        [str(px.index[i]) for i in starts],
        {k: [] for k in STRATEGIES},
        {k: [] for k in STRATEGIES},
        {k: [] for k in STRATEGIES},
    )
    if not starts:
        return ws
    view = np.lib.stride_tricks.sliding_window_view
    PX, ATH, SMA = view(arr, n), view(ath, n), view(sma, n)
    for k in STRATEGIES:
        value, contributed, flows = run_windows(k, PX, ATH, SMA, p)
        ws.multiple[k] = (value[:, -1] / contributed[-1]).tolist()
        ws.irr[k] = irr_rows(flows).tolist()
        ws.mdd[k] = mdd_rows(value).tolist()
    for k in STRATEGIES:
        m = np.array(ws.multiple[k])
        r = np.array(ws.irr[k])
        if not len(m):
            continue
        ws.summary[k] = {
            "multiple_p5": float(np.percentile(m, 5)),
            "multiple_p50": float(np.median(m)),
            "multiple_p95": float(np.percentile(m, 95)),
            "irr_p50": float(np.nanmedian(r)),
            "irr_p5": float(np.nanpercentile(r, 5)),
            "mdd_p50": float(np.median(ws.mdd[k])),
            "mdd_max": float(np.max(ws.mdd[k])),
        }
        ws.worst_start[k] = ws.starts[int(np.argmin(m))]
        if k != "dca":
            ws.dca_win_rate[k] = float(np.mean(np.array(ws.multiple["dca"]) >= m - 1e-12))
    return ws


def single(px: pd.Series, start: str, years: int, p: Params) -> dict[str, Path]:
    i = int(px.index.get_loc(pd.Period(start, freq="M")))  # type: ignore[arg-type]
    n = min(years * 12, len(px) - i)
    arr = px.to_numpy(dtype=float)
    ath, sma = _indicators(px, p.ma_months)
    sl = slice(i, i + n)
    return {k: run_strategy(k, arr[sl], ath[sl], sma[sl], p) for k in STRATEGIES}
