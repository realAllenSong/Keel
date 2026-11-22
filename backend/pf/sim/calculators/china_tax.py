"""Annual 2026 China-resident salary planning using actual entered deductions."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

_SOURCES = [
    "https://www.chinatax.gov.cn/n810219/n810744/n3752930/n3752974/c3970366/content.html",
    "https://etax.liaoning.chinatax.gov.cn/sword?ctrl=SBSfjsqCtrl_initViewGrsdsJsq",
]
_BRACKETS = (
    (0, 0.03, 0),
    (36_000, 0.10, 2_520),
    (144_000, 0.20, 16_920),
    (300_000, 0.25, 31_920),
    (420_000, 0.30, 52_920),
    (660_000, 0.35, 85_920),
    (960_000, 0.45, 181_920),
)


def _money(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_china_employment_tax(
    gross_income_cny: float,
    social_insurance_annual: float = 0,
    housing_fund_annual: float = 0,
    special_deductions_annual: float = 0,
    other_deductions_annual: float = 0,
) -> dict:
    """Salary-only annual liability, all amounts CNY; no city rates are inferred.

    Contributions must be actual deductible employee amounts within applicable
    local rules. Special/other deductions are final eligible tax deductions,
    not additional cash payroll outflows. Annual bonus is included in salary;
    the optional separate annual-bonus tax election is outside this scenario.
    """
    values = {
        "gross_income_cny": gross_income_cny,
        "social_insurance_annual": social_insurance_annual,
        "housing_fund_annual": housing_fund_annual,
        "special_deductions_annual": special_deductions_annual,
        "other_deductions_annual": other_deductions_annual,
    }
    for name, value in values.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
            or value > 1_000_000_000
            or not math.isfinite(value)
        ):
            raise ValueError(f"{name} must be finite and between 0 and 1 billion CNY.")
    contributions = _money(social_insurance_annual + housing_fund_annual)
    if contributions > gross_income_cny:
        raise ValueError("Employee social-insurance and housing-fund contributions exceed wages.")
    taxable = max(
        0.0,
        gross_income_cny
        - 60_000
        - contributions
        - special_deductions_annual
        - other_deductions_annual,
    )
    tax = 0.0
    for lower, rate, quick_deduction in reversed(_BRACKETS):
        if taxable > lower:
            tax = _money(taxable * rate - quick_deduction)
            break
    return {
        "tax_year": 2026,
        "currency": "CNY",
        "tax_context": "china_resident",
        "gross_income": gross_income_cny,
        "social_insurance_annual": social_insurance_annual,
        "housing_fund_annual": housing_fund_annual,
        "special_deductions_annual": special_deductions_annual,
        "other_deductions_annual": other_deductions_annual,
        "employee_contributions": contributions,
        "taxable_income": _money(taxable),
        "income_tax": tax,
        "total_tax": tax,
        "net_income": _money(gross_income_cny - contributions - tax),
        "effective_tax_rate": tax / gross_income_cny if gross_income_cny else 0.0,
        "sources": list(_SOURCES),
        "assumptions": [
            "2026 full-year China tax resident, salary-only comprehensive income, all amounts CNY.",
            "Annual 60000 CNY basic deduction and seven progressive comprehensive-income bands.",
            "Social insurance and housing fund are actual eligible employee annual amounts; no city rates or caps are guessed.",
            "Special additional and other deductions must be independently eligible final deductible amounts; they are not subtracted again from cash.",
            "Annual bonus is combined with comprehensive salary; separate bonus taxation, employer contributions, other income, cross-border credits and subsidies are excluded.",
            "Annual liability estimate, not monthly withholding or a filed return. Sources checked 2026-11-02.",
        ],
    }
