from __future__ import annotations

import datetime as dt
import json
import re
import threading
from dataclasses import asdict
from functools import lru_cache
from typing import Annotated, Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from pf.api.deps import get_conn
from pf.i18n import tr
from pf.market.history import History, history_path, load_history
from pf.reports.balance_sheet import balance_sheet
from pf.reports.cashflow import cashflow, rollup
from pf.reports.common import add_months, latest_month, load_assumptions
from pf.sim import analysis
from pf.sim.engine import Event, Phase, Scenario, cash_bridge, simulate
from pf.sim.planning_basis import build_planning_basis
from pf.sim.returns_model import BLOCKS, ReturnModel, history_stats

router = APIRouter(prefix="/api/sim", tags=["simulation"])
Conn = Annotated[Connection, Depends(get_conn)]


# ---------------------------------------------------------------- schemas
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _offset(month: str, today: dt.date | None = None) -> int:
    """Months from the current month to 'YYYY-MM' (0 = this month)."""
    if not _MONTH.match(month):
        raise HTTPException(400, tr("月份格式应为 YYYY-MM：{m}", m=month))
    t = today or dt.date.today()
    y, m = map(int, month.split("-"))
    return (y - t.year) * 12 + (m - t.month)


class EventIn(BaseModel):
    year: float
    amount: float
    label: str = ""
    every: float | None = None
    times: int = 1
    date: str | None = None  # 'YYYY-MM'; when set it wins over year (so a plan doesn't drift)


class PhaseIn(BaseModel):
    """A life stage in calendar months, both inclusive: school, a gap, a first job."""

    label: str = ""
    start: str
    end: str
    monthly_income: float = Field(default=0, allow_inf_nan=False)
    monthly_spending: float = Field(default=0, allow_inf_nan=False)


class ScenarioIn(BaseModel):
    initial: float
    weights: dict[str, float]
    annual_income: float
    annual_spending: float
    annual_retirement_contribution: float = Field(default=0, ge=0, allow_inf_nan=False)
    retirement_balance: float = Field(default=0, ge=0, allow_inf_nan=False)
    retirement_withdrawal_tax_rate: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    retirement_access_year: float = Field(default=0, ge=0, le=80, allow_inf_nan=False)
    work_years: float
    horizon_years: int = Field(default=50, ge=1, le=80)
    income_growth: float = 0.01
    retirement_spending: float | None = None
    pension: float = 0.0
    pension_start_year: float = 99.0
    withdrawal: Literal["constant", "percent", "guardrails"] = "constant"
    withdrawal_rate: float = 0.04
    rebalance: Literal["monthly", "annual", "none"] = "annual"
    fee: float = 0.001
    expected_return: float | None = None
    events: list[EventIn] = []
    phases: list[PhaseIn] = []

    def to_engine(self, today: dt.date | None = None) -> Scenario:
        d = self.model_dump()
        events = []
        for e in d["events"]:
            date = e.pop("date")
            if date:
                e["year"] = max(0, _offset(date, today)) / 12
            events.append(Event(**e))
        d["events"] = events
        d["phases"] = [
            Phase(
                start=_offset(p["start"], today),
                end=_offset(p["end"], today),
                monthly_income=p["monthly_income"],
                monthly_spending=p["monthly_spending"],
                label=p["label"],
            )
            for p in d["phases"]
        ]
        return Scenario(**d)


class ModelIn(BaseModel):
    kind: Literal["bootstrap", "student_t"] = "bootstrap"
    window_start: str = "1928-01"
    window_end: str | None = None
    block: int = Field(default=12, ge=1, le=120)
    df: float = Field(default=5.0, gt=2.0)
    mean: dict[str, float] | None = None
    vol: dict[str, float] | None = None

    def to_engine(self) -> ReturnModel:
        return ReturnModel(**self.model_dump())


class RunIn(BaseModel):
    scenario: ScenarioIn
    model: ModelIn = ModelIn()
    paths: int = Field(default=10_000, ge=100, le=50_000)
    seed: int = 7


class SimOut(BaseModel):
    years: list[int]
    percentiles: dict[str, list[float]]
    ruin_by_year: list[float]
    success: float
    terminal_mean: float
    terminal_median: float
    lighthouse_targets: list[float]
    lighthouse_prob: list[float]
    lighthouse_median_year: list[float | None]
    mdd_quantiles: dict[str, float]
    mdd_exceed: dict[str, float]
    underwater_months_median: float
    paths: int
    expected_return: float


class BridgeIn(BaseModel):
    scenario: ScenarioIn
    months: int = Field(default=24, ge=1, le=120)


class BridgeMonth(BaseModel):
    month: str
    flow: float  # the month's regular net (phase or steady income minus spending)
    income: float = 0
    spending: float = 0
    events: float  # one-offs that month (tuition, a deposit, a gift)
    labels: list[str]  # which events / phase
    balance: float  # USD cash at the end of the month


class BridgeOut(BaseModel):
    """US-dollar cash month by month under the plan, with no market returns: will the bank
    account carry you through school, a gap and a move?"""

    as_of: str
    start: float  # USD cash minus card balances today
    cash: float
    cards: float
    other: dict[str, float]  # cash in other currencies, native units (not counted)
    rows: list[BridgeMonth]
    low: float
    low_month: str
    short_month: str | None  # first month below zero


class HistoryInfo(BaseModel):
    start: str
    end: str
    approx_note: str
    stats: dict[str, dict[str, float]]


class FactLine(BaseModel):
    category: str
    amount: float  # last 12 months, positive


class Facts(BaseModel):
    """What the ledger shows, with no guess about the future: the plan's income and spending
    are yours to set (an internship ends, a salary starts)."""

    as_of: str
    months: int  # months with any income or spending in the last 12
    investable: float
    income_year: float  # last 12 months (no digits in field names: the app camel-cases them)
    spending_year: float
    income_by: list[FactLine]
    spending_by: list[FactLine]
    recent_income: float  # monthly average of the last 3 months
    recent_spending: float


class PlanningBasisLine(BaseModel):
    category: str
    annual_amount: float
    selected: bool


class PlanningBasis(BaseModel):
    as_of: str
    months: int
    income: list[PlanningBasisLine]
    spending: list[PlanningBasisLine]
    suggested_income: float
    suggested_spending: float
    excluded_income: float
    excluded_spending: float


class EmploymentTaxIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gross_income: float = Field(ge=0, allow_inf_nan=False, strict=True)
    retirement_contribution: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    filing_status: Literal["single", "married_filing_jointly"] = "single"
    residence: Literal["nyc", "nj"] = "nyc"


class ChinaEmploymentTaxIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gross_income_cny: float = Field(ge=0, allow_inf_nan=False, strict=True)
    social_insurance_annual: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    housing_fund_annual: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    special_deductions_annual: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    other_deductions_annual: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)


class NonresidentEmploymentTaxIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gross_income: float = Field(ge=0, allow_inf_nan=False, strict=True)
    retirement_contribution: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    residence: Literal["nyc", "nj"] = "nyc"
    fica_exempt: bool = Field(default=False, strict=True)
    filing_status: Literal["single", "married_filing_separately"] = "single"
    treaty_exempt_income: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    itemized_deductions: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)


class Defaults(BaseModel):
    scenario: ScenarioIn
    model: ModelIn
    history: HistoryInfo | None
    derived_from: str
    facts: Facts | None = None
    planning_basis: PlanningBasis | None = None


class SensOut(BaseModel):
    base_success: float
    base_median: float
    rows: list[dict[str, float | str]]


class RetirementIn(RunIn):
    target: float = 0.95
    max_years: int = 40
    step: int = 1
    curve: bool = True  # False: just the earliest year, by bisection (a few simulations, not 41)


class RetirementOut(BaseModel):
    years: list[int]
    success: list[float]
    earliest: int | None
    target: float


class ReplayOut(BaseModel):
    start: str
    years: list[int]
    wealth: list[float]
    ruined_year: int | None
    complete: bool


class OptimizeIn(RunIn):
    objective: Literal["log", "mean", "median"] = "log"
    max_ruin: float = 0.05
    max_drawdown: float = 0.4
    max_drawdown_prob: float = 0.5
    step: float = Field(default=0.1, ge=0.05, le=0.5)


class PointOut(BaseModel):
    weights: dict[str, float]
    success: float
    e_log: float
    mean: float
    median: float
    p5: float
    mdd_p50: float
    mdd_exceed: float
    feasible: bool


class OptimizeOut(BaseModel):
    objective: str
    points: list[PointOut]
    best: int | None
    current: PointOut


class SavedScenario(BaseModel):
    name: str
    scenario: ScenarioIn
    model: ModelIn


# ---------------------------------------------------------------- helpers
_HISTORY_LOCK = threading.Lock()


@lru_cache(maxsize=2)
def _history_cached(mtime: float) -> History:
    return load_history()


def history() -> pd.DataFrame:
    p = history_path()
    if not p.exists():
        # First use: fetch the 1871+ history once (FRED + Shiller, about 2 MB) instead of failing.
        # The plan page asks several simulations at once: one download, the others wait.
        from pf.market.history import refresh_history

        with _HISTORY_LOCK:
            if not p.exists():
                try:
                    refresh_history()
                except Exception as exc:  # offline, source down
                    raise HTTPException(
                        409, tr("还没有历史行情数据，下载失败：{e}。联网后再试一次。", e=exc)
                    ) from exc
    return _history_cached(p.stat().st_mtime).df


def _sim_out(r) -> SimOut:  # type: ignore[no-untyped-def]
    d = {k: v for k, v in asdict(r).items() if k not in ("terminal", "mdd")}
    return SimOut(**d)


# ---------------------------------------------------------------- endpoints
@router.get("/defaults", response_model=Defaults)
def defaults(conn: Conn) -> Defaults:
    """A starting scenario derived from the ledger: investable assets mapped onto stocks/bonds/
    cash, and reviewable recurring cash-flow suggestions from the trailing 12 months."""
    a = load_assumptions()
    m = latest_month(conn)
    info: HistoryInfo | None = None
    if history_path().exists():
        h = _history_cached(history_path().stat().st_mtime)
        info = HistoryInfo(
            start=str(h.df.index.min()),
            end=str(h.df.index.max()),
            approx_note=tr(
                "1934 年前现金收益按 2%/年近似；Shiller 数据结束后股票/债券由 FRED 序列延伸"
            ),
            stats=history_stats(h.df),
        )
    if not m:
        sc = ScenarioIn(
            initial=0,
            weights={"stocks": 0.8, "bonds": 0.2},
            annual_income=0,
            annual_spending=0,
            work_years=20,
        )
        return Defaults(scenario=sc, model=ModelIn(), history=info, derived_from="empty ledger")
    asof = add_months(m, -1)
    # Where you stand today (a valuation recorded this month counts); cash flows use full months.
    bs = balance_sheet(conn, dt.date.today().isoformat())
    blocks: dict[str, float] = dict.fromkeys(BLOCKS, 0.0)
    total = 0.0
    for cls, cents in bs.by_class.items():
        if cls not in a.fi_asset_classes or cents <= 0:
            continue
        v = cents / 100
        total += v
        for b, w in (a.sim_mapping.get(cls) or {"cash": 1.0}).items():
            blocks[b] += v * w
    weights = (
        {b: round(v / total, 4) for b, v in blocks.items()}
        if total > 0
        else {"stocks": 0.8, "bonds": 0.2, "cash": 0.0}
    )
    drift = 1 - sum(weights.values())
    weights["cash"] = round(weights.get("cash", 0.0) + drift, 4)
    flows = cashflow(conn, add_months(asof, -11), asof)
    basis = PlanningBasis(**build_planning_basis(flows, asof))
    # A new ledger has a few months, not twelve: a starting plan needs a year's worth. (The
    # review sheet still shows the observed totals and lets you choose not to scale them.)
    scale = 12 / basis.months if 0 < basis.months < 12 else 1
    income = basis.suggested_income * scale
    spending = basis.suggested_spending * scale
    facts = _facts(flows, asof, total)
    sc = ScenarioIn(
        initial=round(total, 2),
        weights=weights,
        annual_income=round(income, 2),
        annual_spending=round(spending, 2),
        work_years=20,
        withdrawal_rate=a.withdrawal_rate,
        **_plan_seed(),
    )
    return Defaults(
        scenario=sc,
        model=ModelIn(),
        history=info,
        derived_from=tr("账本截至 {asof}", asof=asof),
        facts=facts,
        planning_basis=basis,
    )


def _plan_seed() -> dict:  # type: ignore[type-arg]
    """Stages and dated events you wrote down in config/plan.local.yaml (personal, not in git)."""
    import yaml

    from pf.settings import get_settings

    path = get_settings().config_dir / "plan.local.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "phases": [
            PhaseIn.model_validate(
                {k: str(v) if k in ("start", "end") else v for k, v in p.items()}
            )
            for p in raw.get("phases") or []
        ],
        "events": [
            EventIn.model_validate(
                {"year": 0, **{k: str(v) if k == "date" else v for k, v in e.items()}}
            )
            for e in raw.get("events") or []
        ],
    }


def _facts(flows: list, asof: str, investable: float) -> Facts:  # type: ignore[type-arg]
    def lines(attr: str, top: int) -> list[FactLine]:
        acc: dict[str, int] = {}
        for f in flows:
            for k, v in getattr(f, attr).items():
                acc[k] = acc.get(k, 0) + v
        if attr == "expenses_by":
            acc = rollup(acc, 2)  # 'Expenses:Food', not every leaf
        best = sorted(acc.items(), key=lambda kv: -kv[1])
        return [FactLine(category=k, amount=round(v / 100, 2)) for k, v in best[:top] if v > 0]

    recent = flows[-3:]
    return Facts(
        as_of=asof,
        months=sum(1 for f in flows if f.income or f.expenses),
        investable=round(investable, 2),
        income_year=round(sum(f.income for f in flows) / 100, 2),
        spending_year=round(sum(f.expenses for f in flows) / 100, 2),
        income_by=lines("income_by", 8),
        spending_by=lines("expenses_by", 8),
        recent_income=round(sum(f.income for f in recent) / 100 / max(1, len(recent)), 2),
        recent_spending=round(sum(f.expenses for f in recent) / 100 / max(1, len(recent)), 2),
    )


@router.post("/china-employment-tax")
def china_employment_tax(body: ChinaEmploymentTaxIn) -> dict:
    from pf.sim.calculators.china_tax import calculate_china_employment_tax

    try:
        return calculate_china_employment_tax(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/nonresident-employment-tax")
def nonresident_employment_tax(body: NonresidentEmploymentTaxIn) -> dict:
    from pf.sim.calculators.nonresident_tax import calculate_nonresident_employment_tax

    try:
        return calculate_nonresident_employment_tax(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/employment-tax")
def employment_tax(body: EmploymentTaxIn) -> dict:
    from pf.sim.calculators.employment_tax import calculate_employment_tax

    try:
        return calculate_employment_tax(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/run", response_model=SimOut)
def run(body: RunIn) -> SimOut:
    try:
        r = simulate(
            body.scenario.to_engine(), body.model.to_engine(), history(), body.paths, body.seed
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _sim_out(r)


def _usd_cash(conn: Connection, date: str) -> tuple[float, float, dict[str, float]]:
    """(USD cash, USD card balances as a positive number, other-currency cash by currency)."""
    bs = balance_sheet(conn, date)
    cash = cards = 0
    other: dict[str, int] = {}
    for line in bs.lines:
        if line.type == "Assets" and line.asset_class == "cash":
            for cur, amt in line.original_balances.items():
                if cur == "USD":
                    cash += amt
                elif len(cur) == 3 and cur.isalpha():
                    other[cur] = other.get(cur, 0) + amt
        elif line.type == "Liabilities":
            cards -= line.original_balances.get("USD", 0)
    return cash / 100, max(0, cards) / 100, {k: v / 100 for k, v in other.items() if v}


def _month_label(today: dt.date, k: int) -> str:
    y, m = divmod(today.year * 12 + today.month - 1 + k, 12)
    return f"{y:04d}-{m + 1:02d}"


@router.post("/bridge", response_model=BridgeOut)
def bridge(body: BridgeIn, conn: Conn) -> BridgeOut:
    today = dt.date.today()
    cash, cards, other = _usd_cash(conn, today.isoformat())
    sc = body.scenario.to_engine(today)
    flows = cash_bridge(sc, body.months)
    names: dict[int, list[str]] = {}
    for p in sc.phases:
        for k in range(max(0, p.start), min(body.months - 1, p.end) + 1):
            names.setdefault(k, []).append(p.label)
    for e in sc.events:
        for i in range(max(1, e.times)):
            k = round((e.year + i * (e.every or 0)) * 12)
            if 0 <= k < body.months and e.label:
                names.setdefault(k, []).append(e.label)
            if not e.every:
                break
    balance = cash - cards
    rows: list[BridgeMonth] = []
    for k, f in enumerate(flows):
        balance += float(f["flow"]) + float(f["events"])
        rows.append(
            BridgeMonth(
                month=_month_label(today, k),
                flow=round(float(f["flow"]), 2),
                income=round(float(f["income"]), 2),
                spending=round(float(f["spending"]), 2),
                events=round(float(f["events"]), 2),
                labels=[n for n in names.get(k, []) if n],
                balance=round(balance, 2),
            )
        )
    low = min(rows, key=lambda r: r.balance)
    short = next((r.month for r in rows if r.balance < 0), None)
    return BridgeOut(
        as_of=today.isoformat(),
        start=round(cash - cards, 2),
        cash=round(cash, 2),
        cards=round(cards, 2),
        other=other,
        rows=rows,
        low=low.balance,
        low_month=low.month,
        short_month=short,
    )


@router.post("/sensitivity", response_model=SensOut)
def sens(body: RunIn) -> SensOut:
    base_s, base_m, rows = analysis.sensitivity(
        body.scenario.to_engine(),
        body.model.to_engine(),
        history(),
        paths=min(body.paths, 3000),
        seed=body.seed,
    )
    return SensOut(base_success=base_s, base_median=base_m, rows=[asdict(r) for r in rows])


@router.post("/retirement", response_model=RetirementOut)
def retirement(body: RetirementIn) -> RetirementOut:
    if not body.curve:
        rc = analysis.earliest_retirement(
            body.scenario.to_engine(),
            body.model.to_engine(),
            history(),
            target=body.target,
            max_years=body.max_years,
            paths=min(body.paths, 1500),
            seed=body.seed,
        )
        return RetirementOut(**asdict(rc))
    rc = analysis.retirement_curve(
        body.scenario.to_engine(),
        body.model.to_engine(),
        history(),
        target=body.target,
        max_years=body.max_years,
        step=body.step,
        paths=min(body.paths, 1500),
        seed=body.seed,
    )
    return RetirementOut(**asdict(rc))


@router.post("/replays", response_model=list[ReplayOut])
def replays(body: RunIn) -> list[ReplayOut]:
    return [ReplayOut(**asdict(r)) for r in analysis.replays(body.scenario.to_engine(), history())]


@router.post("/optimize", response_model=OptimizeOut)
def optimize(body: OptimizeIn) -> OptimizeOut:
    o = analysis.optimize(
        body.scenario.to_engine(),
        body.model.to_engine(),
        history(),
        objective=body.objective,
        max_ruin=body.max_ruin,
        max_drawdown=body.max_drawdown,
        max_drawdown_prob=body.max_drawdown_prob,
        step=body.step,
        paths=min(body.paths, 2000),
        seed=body.seed,
    )
    return OptimizeOut(
        objective=o.objective,
        points=[PointOut(**asdict(p)) for p in o.points],
        best=o.best,
        current=PointOut(**asdict(o.current)),
    )


@router.get("/scenarios", response_model=list[SavedScenario])
def list_scenarios(conn: Conn) -> list[SavedScenario]:
    rows = conn.execute(text("SELECT name, body FROM scenarios ORDER BY updated_at DESC")).all()
    return [SavedScenario(name=r.name, **json.loads(r.body)) for r in rows]


@router.put("/scenarios", response_model=SavedScenario)
def save_scenario(body: SavedScenario, conn: Conn) -> SavedScenario:
    conn.execute(
        text(
            "INSERT INTO scenarios(name, body) VALUES (:n, :b) ON CONFLICT(name) DO UPDATE SET"
            " body = excluded.body, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
        ),
        {
            "n": body.name,
            "b": json.dumps(
                {"scenario": body.scenario.model_dump(), "model": body.model.model_dump()}
            ),
        },
    )
    return body


@router.delete("/scenarios/{name}")
def delete_scenario(name: str, conn: Conn) -> dict[str, bool]:
    conn.execute(text("DELETE FROM scenarios WHERE name = :n"), {"n": name})
    return {"ok": True}
