from importlib import import_module

import pytest


def calculate(*args, **kwargs):
    return import_module("pf.sim.calculators.china_tax").calculate_china_employment_tax(
        *args, **kwargs
    )


@pytest.mark.parametrize(
    ("gross", "tax"),
    [
        (0, 0),
        (60_000, 0),
        (96_000, 1_080),
        (204_000, 11_880),
        (360_000, 43_080),
        (480_000, 73_080),
        (720_000, 145_080),
        (1_020_000, 250_080),
        (1_060_000, 268_080),
    ],
)
def test_statutory_annual_comprehensive_income_schedule(gross, tax):
    result = calculate(gross)
    assert result["income_tax"] == tax
    assert result["tax_year"] == 2026
    assert result["currency"] == "CNY"
    assert result["net_income"] == gross - tax


def test_actual_contributions_reduce_cash_and_tax_base_but_special_deductions_only_reduce_tax():
    result = calculate(300_000, 24_000, 18_000, 24_000, 6_000)
    assert result["taxable_income"] == 168_000
    assert result["income_tax"] == 16_680
    assert result["employee_contributions"] == 42_000
    assert result["net_income"] == 241_320
    assert result["effective_tax_rate"] == pytest.approx(16_680 / 300_000)
    assert result["sources"] and result["assumptions"]


def test_deductions_can_reduce_taxable_income_to_zero_without_creating_negative_tax():
    result = calculate(50_000, special_deductions_annual=50_000)
    assert result["income_tax"] == result["taxable_income"] == 0
    assert result["net_income"] == 50_000
    assert calculate(0)["effective_tax_rate"] == 0


@pytest.mark.parametrize("taxable", [36_000, 144_000, 300_000, 420_000, 660_000, 960_000])
def test_rate_boundaries_have_no_artificial_tax_cliffs(taxable):
    before = calculate(taxable + 60_000 - 0.01)["income_tax"]
    after = calculate(taxable + 60_000 + 0.01)["income_tax"]
    assert 0 <= after - before <= 0.02


@pytest.mark.parametrize(
    "name",
    [
        "gross_income_cny",
        "social_insurance_annual",
        "housing_fund_annual",
        "special_deductions_annual",
        "other_deductions_annual",
    ],
)
@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "100", 10**1000])
def test_rejects_invalid_money_inputs(name, value):
    with pytest.raises(ValueError):
        calculate(**({"gross_income_cny": 100_000} | {name: value}))


def test_rejects_employee_contributions_larger_than_wages():
    with pytest.raises(ValueError):
        calculate(10_000, 8_000, 3_000)
