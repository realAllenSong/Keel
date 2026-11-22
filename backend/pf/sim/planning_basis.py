"""Historical category amounts offered for review as recurring plan assumptions."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from pf.reports.cashflow import MonthFlow
from pf.reports.common import add_months

_UNCERTAIN_SPENDING = (
    "Expenses:Education",
    "Expenses:Travel",
    "Expenses:Cash",
    "Expenses:Uncategorized",
)


def _within_category(category: str, root: str) -> bool:
    return category == root or category.startswith(root + ":")


def build_planning_basis(flows: Iterable[MonthFlow], as_of: str) -> dict[str, Any]:
    """Offer exact ledger categories from the 12 calendar months ending in ``as_of``.

    ``as_of`` and flow months use YYYY-MM. Despite the API name ``annual_amount``,
    rows contain the observed historical total, never an annualized estimate from
    shorter history. ``months`` counts distinct months with nonzero category activity.
    Selected rows are candidates requiring review, not predictions of recurring flows.
    Unselected rows remain visible, including signed refunds and zero net categories.
    """
    start = add_months(as_of, -11)
    income_cents: defaultdict[str, int] = defaultdict(int)
    spending_cents: defaultdict[str, int] = defaultdict(int)
    active_months: set[str] = set()
    for flow in flows:
        if not start <= flow.month <= as_of:
            continue
        if any(flow.income_by.values()) or any(flow.expenses_by.values()):
            active_months.add(flow.month)
        for category, amount in flow.income_by.items():
            income_cents[category] += amount
        for category, amount in flow.expenses_by.items():
            spending_cents[category] += amount

    def lines(
        amounts: dict[str, int], *, income: bool
    ) -> tuple[list[dict[str, Any]], float, float]:
        rows: list[dict[str, Any]] = []
        suggested = excluded = 0
        for category, amount in sorted(amounts.items(), key=lambda item: (-item[1], item[0])):
            selected = (
                _within_category(category, "Income:Salary")
                if income
                else not any(_within_category(category, root) for root in _UNCERTAIN_SPENDING)
            )
            rows.append({"category": category, "annual_amount": amount / 100, "selected": selected})
            if selected:
                suggested += amount
            else:
                excluded += amount
        return rows, suggested / 100, excluded / 100

    income, suggested_income, excluded_income = lines(income_cents, income=True)
    spending, suggested_spending, excluded_spending = lines(spending_cents, income=False)
    return {
        "as_of": as_of,
        "months": len(active_months),
        "income": income,
        "spending": spending,
        "suggested_income": suggested_income,
        "suggested_spending": suggested_spending,
        "excluded_income": excluded_income,
        "excluded_spending": excluded_spending,
    }
