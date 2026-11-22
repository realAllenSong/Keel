from __future__ import annotations

import pytest

from pf.reports.cashflow import MonthFlow
from pf.sim.planning_basis import build_planning_basis


def test_empty_history_has_no_assumed_recurring_flows() -> None:
    assert build_planning_basis([], "2026-10") == {
        "as_of": "2026-10",
        "months": 0,
        "income": [],
        "spending": [],
        "suggested_income": 0.0,
        "suggested_spending": 0.0,
        "excluded_income": 0.0,
        "excluded_spending": 0.0,
    }


def test_only_salary_categories_are_suggested_without_repeating_family_wires() -> None:
    flow = MonthFlow(
        "2026-09",
        income_by={
            "Income:Salary": 100_000,
            "Income:Salary:Employer": 200_000,
            "Income:International Wire": 3_000_000,
            "Income:Family Support": 2_000_000,
            "Income:Uncategorized": 300_000,
            "Income:Dividends": 4_000,
            "Income:Realized": 5_000,
            "Income:Investment": 6_000,
            "Income:SalaryExtra": 7_000,
            "Income:Other:Salary": 8_000,
        },
    )

    basis = build_planning_basis([flow], "2026-10")

    assert {row["category"] for row in basis["income"] if row["selected"]} == {
        "Income:Salary",
        "Income:Salary:Employer",
    }
    assert len(basis["income"]) == 10
    assert basis["suggested_income"] == 3_000.0
    assert basis["excluded_income"] == 53_300.0
    assert next(
        row for row in basis["income"] if row["category"] == "Income:International Wire"
    ) == {
        "category": "Income:International Wire",
        "annual_amount": 30_000.0,
        "selected": False,
    }


def test_partial_salary_history_is_observed_total_without_annualization() -> None:
    flows = [
        MonthFlow("2026-08", income=123_456, income_by={"Income:Salary": 123_456}),
        MonthFlow("2026-09", income=234_567, income_by={"Income:Salary": 234_567}),
        MonthFlow("2026-10"),
    ]

    basis = build_planning_basis(flows, "2026-10")

    assert basis["months"] == 2
    assert basis["suggested_income"] == 3_580.23
    assert basis["income"] == [
        {"category": "Income:Salary", "annual_amount": 3_580.23, "selected": True}
    ]


@pytest.mark.parametrize(
    ("category", "selected"),
    [
        ("Expenses:Education", False),
        ("Expenses:Education:Tuition", False),
        ("Expenses:Travel", False),
        ("Expenses:Travel:Flights", False),
        ("Expenses:Cash", False),
        ("Expenses:Cash:ATM", False),
        ("Expenses:Uncategorized", False),
        ("Expenses:Uncategorized:Unknown", False),
        ("Expenses:EducationExtra", True),
        ("Expenses:TravelExtra", True),
        ("Expenses:Cashmere", True),
        ("Expenses:UncategorizedExtra", True),
        ("Expenses:Other:Education", True),
        ("Expenses:Housing:Rent", True),
    ],
)
def test_uncertain_spending_remains_visible_with_explicit_selection(
    category: str, selected: bool
) -> None:
    basis = build_planning_basis([MonthFlow("2026-09", expenses_by={category: 1_234})], "2026-10")

    assert basis["spending"] == [
        {"category": category, "annual_amount": 12.34, "selected": selected}
    ]
    assert basis["suggested_spending"] == (12.34 if selected else 0.0)
    assert basis["excluded_spending"] == (0.0 if selected else 12.34)


def test_every_exact_category_is_kept_and_reconciles_beyond_eight_largest() -> None:
    flows = [
        MonthFlow(
            "2026-09",
            income_by={"Income:Salary": 100_000, "Income:Family Support": 200_000},
            expenses_by={
                "Expenses:Food:Groceries": 10_001,
                "Expenses:Food:Dining": 9_002,
                "Expenses:Housing": 8_003,
                "Expenses:Utilities": 7_004,
                "Expenses:Health": 6_005,
                "Expenses:Transport": 5_006,
                "Expenses:Entertainment": 4_007,
                "Expenses:Insurance": 3_008,
                "Expenses:Education:Tuition": 2_009,
                "Expenses:Misc": 1,
            },
        ),
        MonthFlow(
            "2026-10",
            income_by={"Income:Salary": 100_000},
            expenses_by={"Expenses:Food:Groceries": 100, "Expenses:Misc": 2},
        ),
    ]

    basis = build_planning_basis(flows, "2026-10")

    spending = {row["category"]: row for row in basis["spending"]}
    assert len(spending) == 10
    assert spending["Expenses:Food:Groceries"]["annual_amount"] == 101.01
    assert spending["Expenses:Food:Dining"]["annual_amount"] == 90.02
    assert spending["Expenses:Misc"]["annual_amount"] == 0.03
    assert basis["suggested_income"] == 2_000.0
    assert basis["excluded_income"] == 2_000.0
    assert basis["suggested_spending"] == 521.39
    assert basis["excluded_spending"] == 20.09
    assert sum(row["annual_amount"] for row in basis["income"]) == pytest.approx(4_000.0)
    assert sum(row["annual_amount"] for row in basis["spending"]) == pytest.approx(541.48)


def test_refunds_and_income_reversals_keep_their_signs() -> None:
    flows = [
        MonthFlow(
            "2026-09",
            income_by={"Income:Salary": -5_000, "Income:Family Support": -1_001},
            expenses_by={"Expenses:Food:Groceries": 10_001},
        ),
        MonthFlow(
            "2026-10",
            expenses_by={
                "Expenses:Food:Groceries": -12_500,
                "Expenses:Education:Tuition": -12_345,
            },
        ),
    ]

    basis = build_planning_basis(flows, "2026-10")

    assert basis["suggested_income"] == -50.0
    assert basis["excluded_income"] == -10.01
    assert basis["suggested_spending"] == -24.99
    assert basis["excluded_spending"] == -123.45
    assert {row["category"]: row["annual_amount"] for row in basis["spending"]} == {
        "Expenses:Food:Groceries": -24.99,
        "Expenses:Education:Tuition": -123.45,
    }


def test_window_contains_only_twelve_calendar_months_through_as_of() -> None:
    flows = [
        MonthFlow("2026-11", income_by={"Income:Salary": 900_000}),
        MonthFlow("2025-10", income_by={"Income:Salary": 800_000}),
        MonthFlow("2026-10", income_by={"Income:Salary": 200_000}),
        MonthFlow("2025-11", income_by={"Income:Salary": 100_000}),
    ]

    basis = build_planning_basis(iter(flows), "2026-10")

    assert basis["as_of"] == "2026-10"
    assert basis["months"] == 2
    assert basis["suggested_income"] == 3_000.0


def test_activity_months_and_zero_net_categories_are_not_lost_to_cancellation() -> None:
    flows = [
        MonthFlow(
            "2026-09",
            income_by={"Income:Salary": 100, "Income:Other": -100},
            expenses_by={"Expenses:Food": 100},
        ),
        MonthFlow("2026-10", expenses_by={"Expenses:Food": -100}),
        MonthFlow("2026-10", income_by={"Income:Salary": 100}),
    ]

    basis = build_planning_basis(flows, "2026-10")

    assert basis["months"] == 2
    assert basis["spending"] == [
        {"category": "Expenses:Food", "annual_amount": 0.0, "selected": True}
    ]
    assert basis["suggested_income"] == 2.0
    assert basis["excluded_income"] == -1.0
