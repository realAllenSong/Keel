"""What brokerage accounts hold, what was traded, and the gains (pf/reports/portfolio.py)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import Connection

from pf.api.deps import get_conn
from pf.reports import goals as gl
from pf.reports import portfolio as pt
from pf.reports import recurring as rc

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])
Conn = Annotated[Connection, Depends(get_conn)]
Currency = Literal["USD", "CNY"]


@router.get("", response_model=pt.Holdings)
def holdings(
    conn: Conn, date: dt.date | None = None, currency: Currency = "USD", live: bool = False
) -> pt.Holdings:
    """Positions on each account's newest broker report (on or before `date`). `live=true`
    values them at delayed market quotes instead of the report's prices."""
    if live and date is None:
        return pt.live_positions(conn, currency)
    return pt.positions(conn, date, currency)


@router.get("/trades", response_model=list[pt.Trade])
def trades(
    conn: Conn,
    start: dt.date | None = None,
    end: dt.date | None = None,
    account: str | None = None,
    symbol: str | None = None,
    include_cash: bool = False,
    limit: int = 500,
) -> list[pt.Trade]:
    rows = pt.trades(
        conn, start=start, end=end, account=account, symbol=symbol, include_cash=include_cash
    )
    return rows[: max(1, min(limit, 5000))]


@router.get("/realized", response_model=pt.Realized)
def realized(conn: Conn, currency: Currency = "USD") -> pt.Realized:
    """Gains on closed lots, first in first out; sales with no known cost listed apart."""
    return pt.realized(conn, currency)


@router.get("/performance", response_model=pt.Performance)
def performance(conn: Conn, currency: Currency = "USD") -> pt.Performance:
    """Money put into positions, taken out and still held; the gain; and the same money in
    QQQ and SPY on the same days. Fetches index closes (cached), so it can take a moment."""
    return pt.performance(conn, currency)


@router.get("/history", response_model=pt.SymbolHistory)
def history(conn: Conn, symbol: str) -> pt.SymbolHistory:
    """One holding's daily closes since its first trade, with its buys and sells."""
    return pt.symbol_history(conn, symbol)


@router.get("/statement")
def statement(
    conn: Conn, start: dt.date, end: dt.date, currency: Currency = "USD"
) -> dict[str, Any]:
    """Accounts, positions at `end`, trades from `start` to `end` (an account certification)."""
    if end < start:
        raise HTTPException(422, "end is before start")
    return pt.statement(conn, start, end, currency)


@router.get("/statement.csv", response_class=PlainTextResponse)
def statement_csv(conn: Conn, start: dt.date, end: dt.date, currency: Currency = "USD") -> str:
    """The same facts as one CSV file a person can open or attach: accounts, then positions at
    `end`, then trades from `start` to `end`."""
    import csv
    import io

    if end < start:
        raise HTTPException(422, "end is before start")
    s = pt.statement(conn, start, end, currency)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Keel", f"{s['from']} to {s['to']}"])
    w.writerow([])
    w.writerow(["ACCOUNTS"])
    w.writerow(
        ["Institution", "Account", "Number ending", "First activity in Keel", "Value", "Currency"]
    )
    for a in s["accounts"]:
        w.writerow(
            [a["institution"], a["account"].rsplit(":", 1)[-1], a["number_ending"],
             a["first_activity"], a["value"], a["currency"]]
        )  # fmt: skip
    w.writerow([])
    w.writerow([f"HOLDINGS (newest broker report on or before {s['to']})"])
    w.writerow(
        [
            "Institution",
            "Account",
            "As of",
            "Symbol",
            "Quantity",
            "Price",
            "Value",
            "Cost",
            "Currency",
        ]
    )
    for acc in s["holdings"]["accounts"]:
        for p in acc["positions"]:
            w.writerow(
                [acc["institution"], acc["account"].rsplit(":", 1)[-1], acc["as_of"], p["name"],
                 p["quantity"], p["price"], p["value"], p["cost"], p["currency"]]
            )  # fmt: skip
    if s["holdings_from_trades"]:
        w.writerow([])
        w.writerow(
            [f"HOLDINGS on {s['to']} worked back from the first broker report and the trades since"]
        )
        w.writerow(["Institution", "Account", "First broker report", "Symbol", "Quantity"])
        for h in s["holdings_from_trades"]:
            w.writerow(
                [h["institution"], h["account"].rsplit(":", 1)[-1], h["from_report"], h["name"], h["quantity"]]
            )  # fmt: skip
    w.writerow([])
    w.writerow(
        [
            f"TRADES ({s['from']} to {s['to']}; {s['cash_movements']} money-market movements not listed)"
        ]
    )
    w.writerow(
        [
            "Date",
            "Institution",
            "Account",
            "Action",
            "Symbol",
            "Quantity",
            "Price",
            "Amount",
            "Currency",
            "Description",
        ]
    )
    for t in s["trades"]:
        w.writerow(
            [t["date"], t["institution"], t["account"].rsplit(":", 1)[-1], t["action"], t["name"],
             t["quantity"], t["price"], t["amount"], t["currency"], t["description"]]
        )  # fmt: skip
    return out.getvalue()


# ---------------------------------------------------------------- 账单: recurring series
bills_router = APIRouter(prefix="/api/bills", tags=["bills"])


class DismissIn(BaseModel):
    key: str
    name: str = ""


@bills_router.get("", response_model=rc.Bills)
def bills(conn: Conn, currency: Currency = "USD") -> rc.Bills:
    """Recurring bills, subscriptions and income found in the ledger, and the dismissed ones."""
    return rc.bills(conn, currency=currency)


@bills_router.post("/dismiss", response_model=rc.Bills)
def dismiss(body: DismissIn, conn: Conn, currency: Currency = "USD") -> rc.Bills:
    """This series is not a bill: no longer listed, forecast or counted as fixed spending."""
    rc.dismiss(conn, body.key, body.name)
    return rc.bills(conn, currency=currency)


@bills_router.delete("/dismiss", response_model=rc.Bills)
def restore(key: str, conn: Conn, currency: Currency = "USD") -> rc.Bills:
    rc.restore(conn, key)
    return rc.bills(conn, currency=currency)


# ---------------------------------------------------------------- savings goals
goals_router = APIRouter(prefix="/api/goals", tags=["goals"])


class GoalIn(BaseModel):
    name: str
    target: str
    currency: Currency = "USD"
    due: dt.date | None = None
    accounts: list[str] = []


def _goals(conn: Connection) -> list[gl.Goal]:
    return gl.goals(conn)


@goals_router.get("", response_model=list[gl.Goal])
def list_goals(conn: Conn) -> list[gl.Goal]:
    return _goals(conn)


@goals_router.post("", response_model=list[gl.Goal])
def add_goal(body: GoalIn, conn: Conn) -> list[gl.Goal]:
    try:
        gl.add(conn, body.name, Decimal(body.target), body.currency, body.due, body.accounts)
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return _goals(conn)


@goals_router.put("/{goal_id}", response_model=list[gl.Goal])
def update_goal(goal_id: int, body: GoalIn, conn: Conn) -> list[gl.Goal]:
    try:
        gl.update(
            conn, goal_id, body.name, Decimal(body.target), body.currency, body.due, body.accounts
        )
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return _goals(conn)


@goals_router.delete("/{goal_id}", response_model=list[gl.Goal])
def delete_goal(goal_id: int, conn: Conn) -> list[gl.Goal]:
    gl.delete(conn, goal_id)
    return _goals(conn)
