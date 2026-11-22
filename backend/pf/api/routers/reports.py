from __future__ import annotations

import datetime as dt
import statistics
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection, text

from pf.api.deps import get_conn
from pf.api.schemas import ReportCurrency
from pf.i18n import tr
from pf.ledger.valuation import set_value
from pf.money import Commodities, from_minor, parse_decimal
from pf.reports.balance_sheet import balance_sheet, networth_series
from pf.reports.cashflow import MonthFlow, cashflow, rollup
from pf.reports.common import Fx, add_months, cents, latest_month, load_assumptions, month_end
from pf.reports.insights import insights
from pf.reports.lighthouses import lighthouses

router = APIRouter(prefix="/api/reports", tags=["reports"])
Conn = Annotated[Connection, Depends(get_conn)]


# ---------------------------------------------------------------- schemas
class Range(BaseModel):
    first: str | None
    last: str | None


class InsightOut(BaseModel):
    kind: str
    severity: str
    title: str
    body: str
    amount: str | None
    filters: dict[str, str]


class MonthSummary(BaseModel):
    month: str
    income: str
    expenses: str
    savings: str
    savings_rate: float | None
    net_worth: str


class CategoryAmount(BaseModel):
    category: str
    amount: str
    prev: str
    avg12: str


class Overview(BaseModel):
    month: str
    currency: str
    current: MonthSummary
    previous: MonthSummary
    top_expenses: list[CategoryAmount]
    series: list[MonthSummary]
    insights: list[InsightOut]
    missing_fx: list[str]


class Row(BaseModel):
    category: str
    values: list[str]
    total: str


class OriginalFlow(BaseModel):
    currency: str
    income: str
    expenses: str
    savings: str


class CashflowTable(BaseModel):
    months: list[str]
    income: list[Row]
    expenses: list[Row]
    income_total: list[str]
    expenses_total: list[str]
    savings: list[str]
    savings_rate: list[float | None]
    original_flows: list[OriginalFlow] = []
    currency: str = "USD"
    missing_fx: list[str] = []


class ExpenseNode(BaseModel):
    category: str
    depth: int
    amount: str
    prev: str
    last_year: str
    avg12: str


class SankeyLink(BaseModel):
    source: str
    target: str
    value: str


class Sankey(BaseModel):
    nodes: list[str]
    links: list[SankeyLink]


class BSLine(BaseModel):
    account: str
    type: str
    asset_class: str | None
    institution: str | None
    balance: str
    note: str | None = None
    original_balances: dict[str, str]


class BalanceSheetOut(BaseModel):
    date: str
    lines: list[BSLine]
    by_class: dict[str, str]
    assets: str
    liabilities: str
    net_worth: str
    currency: str = "USD"
    missing_fx: list[str] = []
    observed_on: dict[str, str] = {}


class NWPoint(BaseModel):
    month: str
    assets: str
    liabilities: str
    net_worth: str
    by_class: dict[str, str]
    currency: str = "USD"
    missing_fx: list[str] = []


class LighthouseOut(BaseModel):
    id: int
    title: str
    target: str
    progress: float
    reached: bool
    years_to_go: float | None
    eta: str | None


class LighthousesOut(BaseModel):
    asof: str
    investable: str
    expected_return: float
    annual_savings: str
    annual_spending: str
    expected_income: str
    withdrawal_rate: float
    by_class: dict[str, str]
    lighthouses: list[LighthouseOut]


class ValuationIn(BaseModel):
    account: str
    date: str
    value: str
    commodity: str = "USD"


class ValuationOut(ValuationIn):
    pass


# ---------------------------------------------------------------- helpers
def _month(conn: Connection, month: str | None) -> str:
    m = month or latest_month(conn)
    if not m:
        raise HTTPException(404, "ledger is empty")
    return m


def _summary(f: MonthFlow, nw: int) -> MonthSummary:
    return MonthSummary(
        month=f.month,
        income=cents(f.income),
        expenses=cents(f.expenses),
        savings=cents(f.savings),
        savings_rate=f.savings_rate,
        net_worth=cents(nw),
    )


# ---------------------------------------------------------------- endpoints
@router.get("/range", response_model=Range)
def months_range(conn: Conn) -> Range:
    r = conn.execute(
        text(
            "SELECT MIN(date), MAX(date) FROM transactions WHERE sealed = 1"
            " AND kind NOT IN ('opening')"
        )
    ).one()
    return Range(first=r[0][:7] if r[0] else None, last=r[1][:7] if r[1] else None)


@router.get("/overview", response_model=Overview)
def overview(
    conn: Conn, month: str | None = None, currency: ReportCurrency | None = None
) -> Overview:
    m = _month(conn, month)
    a = load_assumptions()
    fx = Fx(conn, currency or a.base_currency)
    flows = cashflow(conn, add_months(m, -12), m, fx)
    nws = {p.month: p.net_worth for p in networth_series(conn, add_months(m, -12), m, fx)}
    by = {f.month: f for f in flows}
    cur, prev = by[m], by[add_months(m, -1)]
    hist = [f for f in flows if f.month < m][-12:]

    now2, prev2 = rollup(cur.expenses_by, 2), rollup(prev.expenses_by, 2)
    top = sorted(now2.items(), key=lambda kv: -kv[1])[:8]
    top_out = [
        CategoryAmount(
            category=k,
            amount=cents(v),
            prev=cents(prev2.get(k, 0)),
            avg12=cents(int(statistics.fmean(rollup(f.expenses_by, 2).get(k, 0) for f in hist)))
            if hist
            else "0.00",
        )
        for k, v in top
    ]
    return Overview(
        month=m,
        currency=fx.base,
        current=_summary(cur, nws[m]),
        previous=_summary(prev, nws[add_months(m, -1)]),
        top_expenses=top_out,
        series=[_summary(f, nws[f.month]) for f in flows[1:]],
        insights=[InsightOut(**i.__dict__) for i in insights(conn, m, a)],
        missing_fx=sorted(fx.missing),
    )


@router.get("/cashflow", response_model=CashflowTable)
def cashflow_table(
    conn: Conn,
    start: str | None = None,
    end: str | None = None,
    depth: int = 2,
    currency: ReportCurrency = "USD",
    end_date: dt.date | None = None,
) -> CashflowTable:
    end = _month(conn, end)
    start = start or add_months(end, -11)
    fx = Fx(conn, currency)
    flows = cashflow(conn, start, end, fx, through=end_date.isoformat() if end_date else None)
    months = [f.month for f in flows]

    def rows(attr: str) -> list[Row]:
        per = [rollup(getattr(f, attr), depth) for f in flows]
        cats = sorted({k for p in per for k in p}, key=lambda k: -sum(p.get(k, 0) for p in per))
        return [
            Row(
                category=c,
                values=[cents(p.get(c, 0)) for p in per],
                total=cents(sum(p.get(c, 0) for p in per)),
            )
            for c in cats
        ]

    original_codes = sorted(
        {code for f in flows for code in f.original_income.keys() | f.original_expenses.keys()}
    )
    original_flows = []
    for code in original_codes:
        income = sum((f.original_income.get(code, Decimal(0)) for f in flows), Decimal(0))
        expenses = sum((f.original_expenses.get(code, Decimal(0)) for f in flows), Decimal(0))
        original_flows.append(
            OriginalFlow(
                currency=code,
                income=str(income),
                expenses=str(expenses),
                savings=str(income - expenses),
            )
        )

    return CashflowTable(
        months=months,
        income=rows("income_by"),
        expenses=rows("expenses_by"),
        income_total=[cents(f.income) for f in flows],
        expenses_total=[cents(f.expenses) for f in flows],
        savings=[cents(f.savings) for f in flows],
        savings_rate=[f.savings_rate for f in flows],
        original_flows=original_flows,
        currency=currency,
        missing_fx=sorted(fx.missing),
    )


@router.get("/expenses", response_model=list[ExpenseNode])
def expense_tree(
    conn: Conn,
    month: str | None = None,
    currency: ReportCurrency | None = None,
    end_date: dt.date | None = None,
) -> list[ExpenseNode]:
    return _flow_tree(conn, month, "expenses_by", currency, end_date)


@router.get("/income", response_model=list[ExpenseNode])
def income_tree(
    conn: Conn,
    month: str | None = None,
    currency: ReportCurrency | None = None,
    end_date: dt.date | None = None,
) -> list[ExpenseNode]:
    return _flow_tree(conn, month, "income_by", currency, end_date)


def _flow_tree(
    conn: Connection,
    month: str | None,
    field: Literal["income_by", "expenses_by"],
    currency: ReportCurrency | None,
    end_date: dt.date | None,
) -> list[ExpenseNode]:
    m = _month(conn, month)
    fx = Fx(conn, currency or "USD")
    through = end_date.isoformat() if end_date else None
    flows = {f.month: f for f in cashflow(conn, add_months(m, -12), m, fx, through=through)}
    if currency is not None and fx.missing:
        raise HTTPException(422, tr("缺少汇率：{c}", c=", ".join(sorted(fx.missing))))
    hist = [flows[add_months(m, -i)] for i in range(1, 13)]
    out: list[ExpenseNode] = []
    for depth in (2, 3, 4):
        now = rollup(getattr(flows[m], field), depth)
        prev = rollup(getattr(flows[add_months(m, -1)], field), depth)
        ly = rollup(getattr(flows[add_months(m, -12)], field), depth)
        hs = [rollup(getattr(f, field), depth) for f in hist]
        keys = set(now) | set(prev)
        for k in keys:
            if len(k.split(":")) != depth:
                continue  # deeper levels only exist for accounts that are that deep
            out.append(
                ExpenseNode(
                    category=k,
                    depth=depth,
                    amount=cents(now.get(k, 0)),
                    prev=cents(prev.get(k, 0)),
                    last_year=cents(ly.get(k, 0)),
                    avg12=cents(int(statistics.fmean(h.get(k, 0) for h in hs))),
                )
            )
    return sorted(out, key=lambda n: (n.category.split(":")[:2], n.depth, -float(n.amount)))


@router.get("/sankey", response_model=Sankey)
def sankey(conn: Conn, month: str | None = None, start: str | None = None) -> Sankey:
    """Income sources -> 收入 -> expense categories (2 levels) + 储蓄."""
    end = _month(conn, month)
    flows = cashflow(conn, start or end, end)
    inc: dict[str, int] = {}
    exp: dict[str, int] = {}
    for f in flows:
        for k, v in rollup(f.income_by, 2).items():
            inc[k] = inc.get(k, 0) + v
        for k, v in rollup(f.expenses_by, 2).items():
            exp[k] = exp.get(k, 0) + v
    hub = tr("收入")
    links = [SankeyLink(source=k, target=hub, value=cents(v)) for k, v in inc.items() if v > 0]
    links += [SankeyLink(source=hub, target=k, value=cents(v)) for k, v in exp.items() if v > 0]
    saved = sum(inc.values()) - sum(exp.values())
    if saved > 0:
        links.append(SankeyLink(source=hub, target=tr("储蓄"), value=cents(saved)))
    nodes = sorted({x for link in links for x in (link.source, link.target)})
    return Sankey(nodes=nodes, links=links)


@router.get("/balance-sheet", response_model=BalanceSheetOut)
def balance(
    conn: Conn, date: str | None = None, currency: ReportCurrency = "USD"
) -> BalanceSheetOut:
    d = date or month_end(_month(conn, None)).isoformat()
    fx = Fx(conn, currency)
    bs = balance_sheet(conn, d, fx)
    observations = conn.execute(
        text(
            """
            SELECT a.name, MAX(o.date) AS observed_on
            FROM accounts a JOIN (
                SELECT account_id, date FROM balance_assertions WHERE date <= :d
                UNION ALL
                SELECT a.id, v.date FROM valuations v JOIN accounts a ON a.name = v.account_name
                WHERE v.date <= :d
            ) o ON o.account_id = a.id
            WHERE a.flow_only = 0
            GROUP BY a.id
            """
        ),
        {"d": d},
    )
    commodities = Commodities(conn)
    return BalanceSheetOut(
        date=d,
        lines=[
            BSLine(
                account=x.account,
                type=x.type,
                asset_class=x.asset_class,
                institution=x.institution,
                balance=cents(x.balance),
                note=x.note,
                original_balances={
                    code: str(from_minor(amount, commodities.precision(code)))
                    for code, amount in x.original_balances.items()
                    if amount
                },
            )
            for x in bs.lines
        ],
        by_class={k: cents(v) for k, v in bs.by_class.items()},
        assets=cents(bs.assets),
        liabilities=cents(bs.liabilities),
        net_worth=cents(bs.net_worth),
        currency=currency,
        missing_fx=sorted(fx.missing),
        observed_on={r.name: r.observed_on for r in observations},
    )


@router.get("/networth", response_model=list[NWPoint])
def networth(
    conn: Conn,
    start: str | None = None,
    end: str | None = None,
    currency: ReportCurrency = "USD",
) -> list[NWPoint]:
    end = _month(conn, end)
    first = months_range(conn).first or end
    start = start or max(first, add_months(end, -35))
    fx = Fx(conn, currency)
    points = networth_series(conn, start, end, fx)
    return [
        NWPoint(
            month=p.month,
            assets=cents(p.assets),
            liabilities=cents(p.liabilities),
            net_worth=cents(p.net_worth),
            by_class={k: cents(v) for k, v in p.by_class.items()},
            currency=currency,
            missing_fx=fx.missing_on(month_end(p.month).isoformat()),
        )
        for p in points
    ]


@router.get("/lighthouses", response_model=LighthousesOut)
def get_lighthouses(conn: Conn, month: str | None = None) -> LighthousesOut:
    m = _month(conn, month)
    r = lighthouses(conn, m)
    base = month_end(m)

    def eta(years: float | None) -> str | None:
        if years is None:
            return None
        return (base + dt.timedelta(days=round(years * 365.25))).strftime("%Y-%m")

    return LighthousesOut(
        asof=r.asof,
        investable=cents(r.investable),
        expected_return=r.expected_return,
        annual_savings=cents(r.annual_savings),
        annual_spending=cents(r.annual_spending),
        expected_income=cents(r.expected_income),
        withdrawal_rate=r.withdrawal_rate,
        by_class={k: cents(v) for k, v in r.by_class.items()},
        lighthouses=[
            LighthouseOut(
                id=x.id,
                title=x.title,
                target=cents(x.target),
                progress=x.progress,
                reached=x.reached,
                years_to_go=x.years_to_go,
                eta=eta(x.years_to_go),
            )
            for x in r.lighthouses
        ],
    )


@router.get("/insights", response_model=list[InsightOut])
def get_insights(conn: Conn, month: str | None = None) -> list[InsightOut]:
    return [InsightOut(**i.__dict__) for i in insights(conn, _month(conn, month))]


@router.get("/valuations", response_model=list[ValuationOut])
def list_valuations(conn: Conn) -> list[ValuationOut]:
    rows = conn.execute(
        text(
            "SELECT v.account_name, v.date, v.value, v.commodity, c.precision FROM valuations v"
            " JOIN commodities c ON c.code = v.commodity ORDER BY v.account_name, v.date DESC"
        )
    ).all()
    return [
        ValuationOut(
            account=r.account_name,
            date=r.date,
            value=str(from_minor(r.value, r.precision)),
            commodity=r.commodity,
        )
        for r in rows
    ]


@router.post("/valuations", response_model=ValuationOut)
def add_valuation(body: ValuationIn, conn: Conn) -> ValuationOut:
    if not body.account.startswith(("Assets:", "Liabilities:")):
        raise HTTPException(400, "only Assets/Liabilities accounts can be valued")
    set_value(
        conn,
        body.account,
        dt.date.fromisoformat(body.date),
        parse_decimal(body.value),
        body.commodity,
    )
    return ValuationOut(**body.model_dump())
