"""2026 employment tax fixtures derived from official schedules, not calculator helpers."""

from __future__ import annotations

from importlib import import_module

import pytest


def calculate(*args: object, **kwargs: object) -> dict:
    # Import inside the call so the first TDD run fails on the missing feature,
    # rather than aborting collection of the reference cases.
    return import_module("pf.sim.calculators.employment_tax").calculate_employment_tax(
        *args, **kwargs
    )


@pytest.mark.parametrize(
    ("status", "gross", "expected"),
    [
        ("single", 16_100, 0),
        ("single", 28_500, 1_240),
        ("single", 66_500, 5_800),
        ("single", 121_800, 17_966),
        ("single", 217_875, 41_024),
        ("single", 272_325, 58_448),
        ("single", 656_700, 192_979.25),
        ("married_filing_jointly", 32_200, 0),
        ("married_filing_jointly", 57_000, 2_480),
        ("married_filing_jointly", 133_000, 11_600),
        ("married_filing_jointly", 243_600, 35_932),
        ("married_filing_jointly", 435_750, 82_048),
        ("married_filing_jointly", 544_650, 116_896),
        ("married_filing_jointly", 800_900, 206_583.50),
    ],
)
def test_federal_revenue_procedure_2025_32_schedule_anchors(status, gross, expected):
    assert calculate(gross, filing_status=status)["federal_tax"] == expected


@pytest.mark.parametrize(
    ("status", "gross", "expected"),
    [
        # IT-2105-I (2026), p10 schedule and pp3-5 worksheets 1-11.
        ("single", 58_000, 2_535.40),
        ("single", 128_000, 6_743.56),
        ("single", 200_000, 11_328),
        ("single", 250_000, 15_946.62),
        ("single", 2_000_000, 192_228.43),
        ("single", 6_000_000, 617_176),
        ("single", 26_000_000, 2_833_128),
        ("married_filing_jointly", 66_050, 2_367.40),
        ("married_filing_jointly", 128_000, 5_848.07),
        ("married_filing_jointly", 200_000, 10_666.18),
        ("married_filing_jointly", 400_000, 26_300.38),
        ("married_filing_jointly", 3_000_000, 287_950.90),
        ("married_filing_jointly", 6_000_000, 616_346.85),
        ("married_filing_jointly", 26_000_000, 2_832_250.55),
    ],
)
def test_new_york_2026_rate_schedule_and_every_recapture_worksheet(status, gross, expected):
    assert calculate(gross, filing_status=status)["ny_state_tax"] == expected


@pytest.mark.parametrize("status", ["single", "married_filing_jointly"])
@pytest.mark.parametrize("agi", [107_650, 157_650])
def test_initial_new_york_recapture_endpoints_are_continuous_to_rounding(status, agi):
    before = calculate(agi - 0.01, filing_status=status)["ny_state_tax"]
    at = calculate(agi, filing_status=status)["ny_state_tax"]
    after = calculate(agi + 0.01, filing_status=status)["ny_state_tax"]
    assert abs(at - before) <= 0.02
    assert abs(after - at) <= 0.02


def test_ny_recapture_uses_agi_for_phase_in_and_taxable_income_for_worksheet_selection():
    # At $161,550 taxable MFJ income, worksheet 1 ends. Worksheet 2
    # immediately adds $333 + $807 * ((177600 - 161550) / 50000).
    assert calculate(177_600, filing_status="married_filing_jointly")["ny_state_tax"] == 8_723.70
    assert calculate(177_600.01, filing_status="married_filing_jointly")["ny_state_tax"] == 8_983.05
    # The >$25m NYAGI rule is a real cliff, not another marginal band.
    assert calculate(25_000_000)["ny_state_tax"] == 2_574_176
    assert calculate(25_000_000.01)["ny_state_tax"] == 2_724_128


def test_ny_phase_in_ratio_rounds_to_four_decimals_as_official_worksheet_requires():
    # $107,652.50 -> 0.00005 -> 0.0001, using ordinary half-up rounding.
    assert calculate(107_652.50)["ny_state_tax"] == 5_312.20


@pytest.mark.parametrize(
    ("status", "gross", "before_credit", "school_credit", "net_city"),
    [
        ("single", 108_000, 3_751, 284.64, 3_466.36),
        ("single", 250_000, 9_254.92, 608.40, 8_646.52),
        ("single", 250_001, 9_254.96, 545.40, 8_709.56),
        ("single", 500_000, 18_944.92, 1_115.40, 17_829.52),
        ("single", 500_001, 18_944.96, 0, 18_944.96),
        ("married_filing_jointly", 116_050, 3_651.60, 340.752, 3_310.85),
    ],
)
def test_nyc_published_schedule_and_both_school_credit_amounts(
    status, gross, before_credit, school_credit, net_city
):
    result = calculate(gross, filing_status=status)
    assert result["nyc_tax_before_credits"] == before_credit
    assert result["nyc_school_tax_credit"] == pytest.approx(school_credit, abs=0.005)
    assert result["nyc_tax"] == net_city


def test_school_credit_eligibility_uses_wages_after_traditional_401k():
    result = calculate(520_000, 24_500)
    assert result["nyc_school_tax_credit"] == 1_105.14


@pytest.mark.parametrize(
    ("status", "gross", "expected"),
    [
        ("single", 10_000, 0),
        ("single", 10_001, 126.01),
        ("single", 101_000, 4_243.75),
        ("single", 601_000, 38_693.75),
        ("single", 1_101_000, 85_323.75),
        ("married_filing_jointly", 20_000, 0),
        ("married_filing_jointly", 20_001, 252.01),
        ("married_filing_jointly", 102_000, 2_750),
        ("married_filing_jointly", 602_000, 36_777.50),
        ("married_filing_jointly", 1_102_000, 83_407.50),
    ],
)
def test_nj_regular_exemptions_filing_thresholds_and_current_rate_schedules(
    status, gross, expected
):
    assert (
        calculate(gross, filing_status=status, residence="nj")["nj_tax_before_credit"] == expected
    )


@pytest.mark.parametrize("gross", [0, 15_000, 100_000, 300_000, 1_500_000, 6_000_000])
@pytest.mark.parametrize("status", ["single", "married_filing_jointly"])
def test_same_nyc_job_nj_credit_prevents_double_full_state_tax(gross, status):
    nyc = calculate(gross, filing_status=status)
    nj = calculate(gross, filing_status=status, residence="nj")
    assert nj["ny_state_tax"] == nyc["ny_state_tax"]
    assert nj["nyc_tax"] == 0
    assert nj["nj_credit"] == min(nj["nj_tax_before_credit"], nj["ny_state_tax"])
    assert nj["nj_tax"] + nj["ny_state_tax"] == pytest.approx(
        max(nj["nj_tax_before_credit"], nj["ny_state_tax"]), abs=0.01
    )


def test_401k_reduces_federal_ny_nj_wages_but_not_fica_and_is_not_spendable_cash():
    base = calculate(300_000, residence="nj")
    saved = calculate(300_000, 24_500, residence="nj")
    assert saved["social_security"] == base["social_security"] == 11_439
    assert saved["medicare"] == base["medicare"] == 4_350
    assert saved["additional_medicare"] == base["additional_medicare"] == 900
    assert saved["federal_tax"] < base["federal_tax"]
    assert saved["ny_state_tax"] < base["ny_state_tax"]
    assert saved["nj_tax_before_credit"] < base["nj_tax_before_credit"]
    assert saved["net_income"] == pytest.approx(300_000 - 24_500 - saved["total_tax"])
    assert saved["retirement_tax_savings"] == pytest.approx(
        base["total_tax"] - saved["total_tax"], abs=0.01
    )
    assert saved["net_income"] == pytest.approx(
        base["net_income"] - 24_500 + saved["retirement_tax_savings"], abs=0.01
    )


@pytest.mark.parametrize(
    ("gross", "status", "social_security", "medicare", "additional"),
    [
        (184_500, "single", 11_439, 2_675.25, 0),
        (200_000, "single", 11_439, 2_900, 0),
        (250_000, "single", 11_439, 3_625, 450),
        (250_000, "married_filing_jointly", 11_439, 3_625, 0),
        (300_000, "married_filing_jointly", 11_439, 4_350, 450),
    ],
)
def test_fica_uses_one_employees_ssa_cap_and_actual_filing_status_for_additional_medicare(
    gross, status, social_security, medicare, additional
):
    result = calculate(gross, filing_status=status)
    assert result["social_security"] == social_security
    assert result["medicare"] == medicare
    assert result["additional_medicare"] == additional


@pytest.mark.parametrize("status", ["single", "married_filing_jointly"])
@pytest.mark.parametrize("residence", ["nyc", "nj"])
def test_zero_income_has_no_negative_taxes_or_spendable_wages(status, residence):
    result = calculate(0, filing_status=status, residence=residence)
    for name in (
        "federal_tax",
        "social_security",
        "medicare",
        "additional_medicare",
        "ny_state_tax",
        "nyc_tax",
        "nj_tax_before_credit",
        "nj_tax",
        "total_tax",
        "net_income",
        "effective_tax_rate",
        "retirement_tax_savings",
    ):
        assert result[name] == 0
    assert result["tax_year"] == 2026
    assert result["assumptions"]
    assert all(source.startswith("https://") for source in result["sources"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"gross_income": -1},
        {"gross_income": float("nan")},
        {"gross_income": float("inf")},
        {"gross_income": True},
        {"gross_income": "100000"},
        {"gross_income": 1_000_000_001},
        {"gross_income": 10**1000},
        {"retirement_contribution": -1},
        {"retirement_contribution": float("nan")},
        {"retirement_contribution": float("inf")},
        {"retirement_contribution": 24_500.01},
        {"gross_income": 100, "retirement_contribution": 101},
        {"gross_income": 1_000, "retirement_contribution": 1_000},
        {"filing_status": "head_of_household"},
        {"filing_status": "married_filing_separately"},
        {"residence": "lic"},
        {"residence": "ca"},
        {"work_location": "nj"},
    ],
)
def test_rejects_invalid_or_unsupported_cases_with_manual_takehome_guidance(kwargs):
    with pytest.raises(ValueError, match="manual"):
        calculate(**({"gross_income": 100_000} | kwargs))
