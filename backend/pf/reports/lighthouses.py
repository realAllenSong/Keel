"""The three lighthouses (巫师《原始积累的 5 条建议》):

  1. expected annual return on assets >= annual new savings   (资产增值的权重 = 储蓄的权重)
  2. expected annual return on assets >= annual living costs  (理论上可以摆脱主动收入)
  3. assets x withdrawal rate (4%)    >= annual living costs  (财务独立)

Current position comes from the ledger: investable assets (fi_asset_classes) at the as-of date,
savings and spending over the trailing 12 months. "Years to go" is a deterministic projection at
the expected return with savings held constant — the Monte Carlo view comes in M7."""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import Connection

from pf.i18n import tr
from pf.reports.balance_sheet import balance_sheet
from pf.reports.cashflow import cashflow
from pf.reports.common import Assumptions, Fx, add_months, load_assumptions, month_end


@dataclass
class Lighthouse:
    id: int
    title: str
    target: int  # cents: the asset level at which it is reached
    progress: float  # current assets / target (capped at 1)
    reached: bool
    years_to_go: float | None  # None = unreachable with current savings & return


@dataclass
class LighthouseReport:
    asof: str
    investable: int  # cents
    expected_return: float  # blended, annual
    annual_savings: int  # trailing 12m, cents
    annual_spending: int
    expected_income: int  # investable * expected_return
    withdrawal_rate: float
    lighthouses: list[Lighthouse]
    by_class: dict[str, int]


def years_to_reach(
    start: float, contribution: float, rate: float, target: float, periods_per_year: int = 12
) -> float | None:
    """Years until assets >= target when investing `contribution` per year at `rate` per year,
    contributions spread over `periods_per_year` and made at the end of each period."""
    if start >= target:
        return 0.0
    if contribution <= 0 and rate <= 0:
        return None
    n = periods_per_year
    r = (1 + rate) ** (1 / n) - 1 if rate > -1 else -1
    c = contribution / n
    if abs(r) < 1e-12:
        return (target - start) / contribution if contribution > 0 else None
    # A_k = (A0 + c/r)(1+r)^k - c/r  =>  k = ln((T + c/r)/(A0 + c/r)) / ln(1+r)
    num, den = target + c / r, start + c / r
    if den <= 0 or num / den <= 0:
        return None
    k = math.log(num / den) / math.log(1 + r)
    if k < 0 or math.isnan(k):
        return None
    k = math.ceil(k - 1e-9) if n > 1 else k
    return k / n


def targets(
    annual_savings: float, annual_spending: float, rate: float, withdrawal: float
) -> list[float]:
    inf = float("inf")
    return [
        annual_savings / rate if rate > 0 else inf,
        annual_spending / rate if rate > 0 else inf,
        annual_spending / withdrawal if withdrawal > 0 else inf,
    ]


TITLES = (
    "资产预期年收益 ≥ 每年新增储蓄",
    "资产预期年收益 ≥ 每年生活开销",
    "按 4% 提款可覆盖每年开销（财务独立）",
)


def lighthouses(
    conn: Connection, asof_month: str, a: Assumptions | None = None
) -> LighthouseReport:
    a = a or load_assumptions()
    fx = Fx(conn, a.base_currency)
    bs = balance_sheet(conn, month_end(asof_month).isoformat(), fx)
    by_class = {k: v for k, v in bs.by_class.items() if k in a.fi_asset_classes and v > 0}
    investable = sum(by_class.values())
    blended = (
        sum(v * a.expected(k) for k, v in by_class.items()) / investable
        if investable > 0
        else a.expected("brokerage")
    )
    flows = cashflow(conn, add_months(asof_month, -11), asof_month, fx)
    savings = sum(f.savings for f in flows)
    spending = sum(f.expenses for f in flows)

    out = []
    for i, (title, tgt) in enumerate(
        zip(map(tr, TITLES), targets(savings, spending, blended, a.withdrawal_rate), strict=True), 1
    ):
        reached = investable >= tgt
        out.append(
            Lighthouse(
                id=i,
                title=title.replace("4%", f"{a.withdrawal_rate:.0%}"),
                target=int(tgt) if math.isfinite(tgt) else 0,
                progress=min(1.0, investable / tgt) if math.isfinite(tgt) and tgt > 0 else 0.0,
                reached=reached,
                years_to_go=years_to_reach(investable, savings, blended, tgt)
                if math.isfinite(tgt)
                else None,
            )
        )
    return LighthouseReport(
        asof=asof_month,
        investable=investable,
        expected_return=blended,
        annual_savings=savings,
        annual_spending=spending,
        expected_income=int(investable * blended),
        withdrawal_rate=a.withdrawal_rate,
        lighthouses=out,
        by_class=by_class,
    )
