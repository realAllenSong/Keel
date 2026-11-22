from importlib import import_module

import pytest


def calculate(*args, **kwargs):
    return import_module("pf.sim.calculators.nonresident_tax").calculate_nonresident_employment_tax(
        *args, **kwargs
    )


@pytest.mark.parametrize(
    ("status", "gross", "expected"),
    [
        ("single", 10_000, 1_000),
        ("single", 100_000, 16_712),
        ("single", 500_000, 143_769.25),
        ("single", 700_000, 214_957.25),
        ("married_filing_separately", 100_000, 16_712),
        ("married_filing_separately", 384_350, 103_291.75),
        ("married_filing_separately", 500_000, 146_082.25),
    ],
)
def test_no_automatic_standard_deduction_and_correct_2026_single_mfs_schedules(
    status, gross, expected
):
    result = calculate(gross, filing_status=status)
    assert result["federal_tax"] == expected
    assert result["federal_standard_deduction"] == 0
    assert result["tax_context"] == "us_nonresident"
    assert result["currency"] == "USD"


def test_confirmed_treaty_exemption_reduces_federal_ny_and_city_but_not_fica():
    full = calculate(100_000)
    result = calculate(100_000, treaty_exempt_income=5_000)
    assert result["federal_tax"] == 15_612
    assert result["ny_state_tax"] == 4_565.65
    assert result["nyc_tax"] < full["nyc_tax"]
    assert result["social_security"] == full["social_security"] == 6_200
    assert result["medicare"] == full["medicare"] == 1_450
    assert calculate(100_000)["treaty_exempt_income"] == 0


def test_nj_treaty_wages_remain_taxable_and_credit_uses_only_income_taxed_by_both_states():
    result = calculate(100_000, residence="nj", treaty_exempt_income=5_000)
    assert result["ny_state_tax"] == 4_565.65
    assert result["nj_tax_before_credit"] == 4_180.05
    assert result["nj_credit_income_fraction"] == 0.95
    assert result["nj_credit"] == 3_971.05
    assert result["nj_tax"] == 209
    assert result["nyc_tax"] == 0
    entirely_exempt = calculate(
        20_000, residence="nj", treaty_exempt_income=20_000, fica_exempt=True
    )
    assert entirely_exempt["federal_tax"] == entirely_exempt["ny_state_tax"] == 0
    assert entirely_exempt["nj_tax"] == 266


@pytest.mark.parametrize("status", ["single", "married_filing_separately"])
def test_explicit_qualified_fica_exemption_removes_all_employee_fica(status):
    subject = calculate(300_000, filing_status=status)
    exempt = calculate(300_000, filing_status=status, fica_exempt=True)
    for key in ("social_security", "medicare", "additional_medicare"):
        assert exempt[key] == 0
        assert subject[key] > 0
    assert exempt["federal_tax"] == subject["federal_tax"]
    assert exempt["net_income"] > subject["net_income"]


def test_additional_medicare_uses_married_separate_125000_threshold():
    assert (
        calculate(150_000, filing_status="married_filing_separately")["additional_medicare"] == 225
    )
    assert calculate(150_000)["additional_medicare"] == 0


def test_itemized_deduction_is_explicit_final_amount_and_not_automatic_state_tax_deduction():
    base = calculate(100_000)
    itemized = calculate(100_000, itemized_deductions=10_000)
    assert itemized["federal_tax"] == 14_512
    assert base["federal_tax"] - itemized["federal_tax"] == 2_200
    assert itemized["ny_state_tax"] == base["ny_state_tax"]
    assert itemized["nyc_tax"] == base["nyc_tax"]
    assert itemized["net_income"] - base["net_income"] == 2_200


def test_401k_cash_exclusion_and_tax_savings_are_consistent_in_treaty_nj_case():
    base = calculate(300_000, residence="nj", treaty_exempt_income=5_000)
    result = calculate(300_000, 24_500, residence="nj", treaty_exempt_income=5_000)
    assert result["net_income"] == pytest.approx(300_000 - 24_500 - result["total_tax"])
    assert result["retirement_tax_savings"] == pytest.approx(
        base["total_tax"] - result["total_tax"], abs=0.01
    )
    assert result["social_security"] == base["social_security"]
    assert result["federal_agi"] == 270_500


@pytest.mark.parametrize("residence", ["nyc", "nj"])
def test_zero_wages_has_nonnegative_zero_tax_and_takehome(residence):
    result = calculate(0, residence=residence)
    assert result["total_tax"] == result["net_income"] == result["effective_tax_rate"] == 0
    assert result["sources"] and result["assumptions"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"gross_income": -1},
        {"gross_income": float("nan")},
        {"gross_income": float("inf")},
        {"gross_income": 10**1000},
        {"gross_income": 1_000_000_001},
        {"gross_income": True},
        {"retirement_contribution": -1},
        {"retirement_contribution": 24_501},
        {"retirement_contribution": float("nan")},
        {"treaty_exempt_income": -1},
        {"treaty_exempt_income": 100_001},
        {"retirement_contribution": 20_000, "treaty_exempt_income": 90_000},
        {"itemized_deductions": float("nan")},
        {"itemized_deductions": -1},
        {"itemized_deductions": 100_001},
        {"fica_exempt": 1},
        {"fica_exempt": "yes"},
        {"residence": "ca"},
        {"filing_status": "married_filing_jointly"},
        {"gross_income": 1_000, "retirement_contribution": 1_000},
        # A final total cannot tell us which deductions need AMT addbacks.
        {
            "gross_income": 200_000,
            "filing_status": "married_filing_separately",
            "itemized_deductions": 50_000,
        },
        {"gross_income": 600_000, "itemized_deductions": 10_000},
    ],
)
def test_rejects_invalid_and_out_of_scope_minimum_tax_cases_with_manual_guidance(kwargs):
    with pytest.raises(ValueError, match="manual"):
        calculate(**({"gross_income": 100_000} | kwargs))
