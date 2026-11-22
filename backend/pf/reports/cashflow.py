"""Household cash-flow statement: where money came from, where it went, what was kept.

Only real flows count: valuation adjustments (unrealised gains) and opening balances are not
cash flow; internal transfers never touch Income/Expenses so they are excluded by construction."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import Connection, text

from pf.reports.common import Fx, month_range, month_rate_date, month_start


@dataclass
class MonthFlow:
    month: str
    income: int = 0  # base-currency cents, positive
    expenses: int = 0  # positive
    income_by: dict[str, int] = field(default_factory=dict)
    expenses_by: dict[str, int] = field(default_factory=dict)
    original_income: dict[str, Decimal] = field(default_factory=dict)
    original_expenses: dict[str, Decimal] = field(default_factory=dict)

    @property
    def savings(self) -> int:
        return self.income - self.expenses

    @property
    def savings_rate(self) -> float | None:
        return self.savings / self.income if self.income > 0 else None


def cashflow(
    conn: Connection, start: str, end: str, fx: Fx | None = None, *, through: str | None = None
) -> list[MonthFlow]:
    fx = fx or Fx(conn)
    months = {m: MonthFlow(m) for m in month_range(start, end)}
    rows = conn.execute(
        text(
            """
            SELECT substr(date, 1, 7) AS m, account, account_type, commodity, precision, SUM(amount) AS amt
            FROM v_postings
            WHERE account_type IN ('Income', 'Expenses')
              AND kind NOT IN ('adjustment', 'opening')
              AND date BETWEEN :s AND :e
            GROUP BY m, account, commodity
            """
        ),
        {"s": month_start(start).isoformat(), "e": month_rate_date(end, through)},
    ).all()
    inc: dict[str, defaultdict[str, int]] = {m: defaultdict(int) for m in months}
    exp: dict[str, defaultdict[str, int]] = {m: defaultdict(int) for m in months}
    for r in rows:
        v = fx.to_base(r.amt, r.commodity, month_rate_date(r.m, through))
        native = Decimal(r.amt).scaleb(-r.precision)
        if r.account_type == "Income":
            inc[r.m][r.account] += -v  # income postings are credits (negative)
            original = months[r.m].original_income
            original[r.commodity] = original.get(r.commodity, Decimal(0)) - native
        else:
            exp[r.m][r.account] += v
            original = months[r.m].original_expenses
            original[r.commodity] = original.get(r.commodity, Decimal(0)) + native
    for m, mf in months.items():
        mf.income_by = {k: v for k, v in inc[m].items() if v}
        mf.expenses_by = {k: v for k, v in exp[m].items() if v}
        mf.income = sum(mf.income_by.values())
        mf.expenses = sum(mf.expenses_by.values())
    return list(months.values())


def rollup(by_account: dict[str, int], depth: int) -> dict[str, int]:
    """'Expenses:Food:Groceries' at depth 2 -> 'Expenses:Food'."""
    out: defaultdict[str, int] = defaultdict(int)
    for k, v in by_account.items():
        out[":".join(k.split(":")[:depth])] += v
    return dict(out)
