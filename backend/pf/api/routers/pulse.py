"""This month so far, the financial calendar and alerts (see pf.reports.pulse)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection

from pf.api.deps import get_conn
from pf.api.schemas import ReportCurrency
from pf.i18n import tr
from pf.reports.common import Fx, cents
from pf.reports.pulse import cash_history, pulse

router = APIRouter(prefix="/api", tags=["pulse"])
Conn = Annotated[Connection, Depends(get_conn)]


class PulseDay(BaseModel):
    day: int
    actual: str | None
    typical: str
    low: str
    high: str


class PulseEvent(BaseModel):
    date: str
    name: str
    amount: str
    kind: Literal["bill", "income", "card_payment", "transfer"]
    account: str
    cadence: Literal["monthly", "annual"]
    category: str | None
    counterparty: str | None
    estimated: bool
    overdue: bool


class CashDay(BaseModel):
    date: str
    balance: str


class CashFlowDayOut(BaseModel):
    date: str
    balance: str
    inflow: str
    outflow: str


class PulseAlert(BaseModel):
    id: str
    severity: Literal["warning", "notice", "info"]
    title: str
    body: str
    href: str | None


class PulseOut(BaseModel):
    has_data: bool
    currency: str = "USD"
    missing_fx: list[str] = []
    as_of: str | None = None
    month: str | None = None
    days_in_month: int = 0
    history_months: int = 0
    spent: str = "0"
    typical_to_date: str = "0"
    typical_month: str = "0"
    budget: str = "0"
    budget_source: Literal["set", "typical"] = "typical"
    forecast: str = "0"
    remaining_bills: str = "0"
    safe_per_day: str | None = None
    curve: list[PulseDay] = []
    upcoming: list[PulseEvent] = []
    cash_now: str = "0"
    cash_low: str = "0"
    cash_low_date: str | None = None
    low_balance: str = "0"
    cash_series: list[CashDay] = []
    alerts: list[PulseAlert] = []


@router.get("/pulse", response_model=PulseOut)
def get_pulse(
    conn: Conn, as_of: dt.date | None = None, currency: ReportCurrency = "USD"
) -> PulseOut:
    fx = Fx(conn, currency)
    p = pulse(conn, as_of, fx)
    if p is None:
        return PulseOut(has_data=False, currency=currency)
    return PulseOut(
        has_data=True,
        currency=currency,
        missing_fx=sorted(fx.missing),
        as_of=p.as_of.isoformat(),
        month=p.month,
        days_in_month=p.days_in_month,
        history_months=p.history_months,
        spent=cents(p.spent),
        typical_to_date=cents(p.typical_to_date),
        typical_month=cents(p.typical_month),
        budget=cents(p.budget),
        budget_source=p.budget_source,  # type: ignore[arg-type]
        forecast=cents(p.forecast),
        remaining_bills=cents(p.remaining_bills),
        safe_per_day=cents(p.safe_per_day) if p.safe_per_day is not None else None,
        curve=[
            PulseDay(
                day=c.day,
                actual=cents(c.actual) if c.actual is not None else None,
                typical=cents(c.typical),
                low=cents(c.low),
                high=cents(c.high),
            )
            for c in p.curve
        ],
        upcoming=[
            PulseEvent(
                date=e.date.isoformat(),
                name=e.name,
                amount=cents(e.amount),
                kind=e.kind,  # type: ignore[arg-type]
                account=e.account,
                cadence=e.cadence,  # type: ignore[arg-type]
                category=e.category,
                counterparty=e.counterparty,
                estimated=e.estimated,
                overdue=e.overdue,
            )
            for e in p.upcoming
        ],
        cash_now=cents(p.cash_now),
        cash_low=cents(p.cash_low),
        cash_low_date=p.cash_low_date.isoformat() if p.cash_low_date else None,
        low_balance=cents(p.low_balance),
        cash_series=[
            CashDay(date=c.date.isoformat(), balance=cents(c.balance)) for c in p.cash_series
        ],
        alerts=[
            PulseAlert(id=a.id, severity=a.severity, title=a.title, body=a.body, href=a.href)  # type: ignore[arg-type]
            for a in p.alerts
        ],
    )


@router.get("/cashline", response_model=list[CashFlowDayOut])
def get_cashline(
    conn: Conn, start: dt.date, end: dt.date, currency: ReportCurrency | None = None
) -> list[CashFlowDayOut]:
    """The cash accounts day by day, with each day's money in and out (the timelines)."""
    if end < start or (end - start).days > 400:
        return []
    fx = Fx(conn, currency or "USD")
    days = cash_history(conn, start, end, fx)
    if currency is not None and fx.missing:
        raise HTTPException(422, tr("缺少汇率：{c}", c=", ".join(sorted(fx.missing))))
    return [
        CashFlowDayOut(
            date=d.date.isoformat(),
            balance=cents(d.balance),
            inflow=cents(d.inflow),
            outflow=cents(d.outflow),
        )
        for d in days
    ]


@router.get("/in-flight")
def get_in_flight(conn: Conn, currency: ReportCurrency = "USD") -> dict:
    """What the banks show but have not posted: pending lines and money on its way in. Shown
    beside the books, never counted in them (pf/ledger/in_flight.py)."""
    from pf.ledger.in_flight import as_dict, in_flight

    return as_dict(in_flight(conn, currency))
