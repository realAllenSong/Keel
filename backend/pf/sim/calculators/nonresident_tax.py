"""Bounded 2026 regular wage-tax estimate for full-year federal nonresidents."""

from __future__ import annotations

import math

from pf.sim.calculators.employment_tax import (
    _FEDERAL,
    MAX_GROSS_INCOME,
    RETIREMENT_LIMIT,
    SOURCES,
    _income_taxes,
    _rounded,
    _schedule,
)

_MFS = "married_filing_separately"
_MFS_RATES = (
    (0, 0, 0.10),
    (12_400, 1_240, 0.12),
    (50_400, 5_800, 0.22),
    (105_700, 17_966, 0.24),
    (201_775, 41_024, 0.32),
    (256_225, 58_448, 0.35),
    (384_350, 103_291.75, 0.37),
)
_SOURCES = (
    "https://www.irs.gov/instructions/i1040nr",
    "https://www.irs.gov/publications/p519",
    "https://www.irs.gov/pub/irs-trty/china.pdf",
    "https://www.irs.gov/individuals/international-taxpayers/foreign-student-liability-for-social-security-and-medicare-taxes",
    "https://www.tax.ny.gov/forms/publications/wt/nys50.htm",
    "https://www.tax.ny.gov/pdf/advisory_opinions/income/a10_7i.pdf",
    "https://www.irs.gov/irb/2025-45_IRB#REV-PROC-2025-32",
    "https://www.irs.gov/instructions/i6251",
)
_FALLBACK = " Use manual annual take-home when these bounded assumptions do not apply."


def _check_money(name: str, value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or value > MAX_GROSS_INCOME
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be finite and between $0 and $1 billion." + _FALLBACK)


def _check_amt_scope(agi: float, deduction: float, federal_tax: float, status: str) -> None:
    """Accept only where even adding back all deductions cannot trigger AMT.

    With salary alone there are no other AMT preferences. This screen is
    conservative: charitable deductions may be deductible for AMT, but a
    single final Schedule A total does not identify their composition.
    """
    if not deduction:
        return
    exemption = 70_100 if status == _MFS else 90_100
    excess = max(0.0, agi - exemption)
    breakpoint = 122_250 if status == _MFS else 244_500
    upper_tentative = 0.26 * min(excess, breakpoint) + 0.28 * max(0.0, excess - breakpoint)
    if _rounded(upper_tentative) > federal_tax:
        raise ValueError(
            "Itemized deductions may trigger AMT; this needs a Form 6251 calculation." + _FALLBACK
        )


def _amounts(
    gross: float,
    contribution: float,
    residence: str,
    fica_exempt: bool,
    status: str,
    treaty: float,
    deduction: float,
) -> dict:
    agi = gross - contribution - treaty
    taxable = max(0.0, agi - deduction)
    federal = _rounded(_schedule(taxable, _MFS_RATES if status == _MFS else _FEDERAL["single"]))
    _check_amt_scope(agi, deduction, federal, status)
    # NY and NYC honor eligible federal treaty wage exclusions. Both single
    # and MFS use the NY single/MFS schedule and $8,000 NY standard deduction.
    taxes = _income_taxes(gross - treaty, contribution, "single", residence)
    taxes["federal_tax"] = federal
    fraction = 0.0
    if residence == "nj":
        nj_agi = gross - contribution
        nj_before = _income_taxes(gross, contribution, "single", "nj")["nj_tax_before_credit"]
        # NJ taxes wages independently of federal AGI. Treaty-excluded wages
        # remain NJ wages. NJ-COJ includes only wages taxed by BOTH states.
        fraction = agi / nj_agi if nj_agi else 0.0
        credit = min(taxes["ny_state_tax"], _rounded(nj_before * fraction))
        taxes.update(
            nj_tax_before_credit=nj_before, nj_credit=credit, nj_tax=_rounded(nj_before - credit)
        )
    social = 0.0 if fica_exempt else _rounded(min(gross, 184_500) * 0.062)
    medicare = 0.0 if fica_exempt else _rounded(gross * 0.0145)
    additional_threshold = 125_000 if status == _MFS else 200_000
    additional = 0.0 if fica_exempt else _rounded(max(0.0, gross - additional_threshold) * 0.009)
    total = _rounded(
        sum(taxes[key] for key in ("federal_tax", "ny_state_tax", "nyc_tax", "nj_tax"))
        + social
        + medicare
        + additional
    )
    return {
        **taxes,
        "federal_agi": _rounded(agi),
        "federal_taxable_income": _rounded(taxable),
        "federal_standard_deduction": 0.0,
        "social_security": social,
        "medicare": medicare,
        "additional_medicare": additional,
        "nj_credit_income_fraction": fraction,
        "total_tax": total,
        "net_income": _rounded(gross - contribution - total),
    }


def calculate_nonresident_employment_tax(
    gross_income: float,
    retirement_contribution: float = 0,
    residence: str = "nyc",
    fica_exempt: bool = False,
    filing_status: str = "single",
    treaty_exempt_income: float = 0,
    itemized_deductions: float = 0,
) -> dict:
    """One full-year NYC wage, full-year federal NRA, single or MFS.

    Residency and FICA/treaty eligibility must be determined independently.
    itemized_deductions is the FINAL allowable federal Schedule A amount,
    after SALT/category/2026 limitations and allocation to nonexempt ECI.
    No state tax payment or standard deduction is inferred automatically.
    """
    for name, value in (
        ("gross_income", gross_income),
        ("retirement_contribution", retirement_contribution),
        ("treaty_exempt_income", treaty_exempt_income),
        ("itemized_deductions", itemized_deductions),
    ):
        _check_money(name, value)
    if filing_status not in ("single", _MFS):
        raise ValueError(
            "Nonresident scenario supports single or married_filing_separately." + _FALLBACK
        )
    if residence not in ("nyc", "nj"):
        raise ValueError(
            "Supported full-year residences are nyc and nj; job must be in NYC." + _FALLBACK
        )
    if not isinstance(fica_exempt, bool):
        raise ValueError("fica_exempt must be an explicit eligibility boolean." + _FALLBACK)
    if (
        retirement_contribution > RETIREMENT_LIMIT
        or retirement_contribution + treaty_exempt_income > gross_income
    ):
        raise ValueError(
            "401(k)/treaty exclusions exceed wages or the regular $24,500 deferral limit."
            + _FALLBACK
        )
    if itemized_deductions > gross_income - retirement_contribution - treaty_exempt_income:
        raise ValueError(
            "Final allowable itemized deductions exceed nonexempt wage AGI." + _FALLBACK
        )
    if itemized_deductions and gross_income - treaty_exempt_income > 500_000:
        raise ValueError(
            "Positive itemized deductions above $500,000 nonexempt wages require an AMT phaseout review."
            + _FALLBACK
        )
    result = _amounts(
        gross_income,
        retirement_contribution,
        residence,
        fica_exempt,
        filing_status,
        treaty_exempt_income,
        itemized_deductions,
    )
    if result["net_income"] < 0:
        raise ValueError(
            "401(k) deferral leaves insufficient wages for employee taxes." + _FALLBACK
        )
    baseline = _amounts(
        gross_income,
        0,
        residence,
        fica_exempt,
        filing_status,
        treaty_exempt_income,
        itemized_deductions,
    )
    return {
        "tax_year": 2026,
        "currency": "USD",
        "tax_context": "us_nonresident",
        "gross_income": gross_income,
        "retirement_contribution": retirement_contribution,
        "filing_status": filing_status,
        "residence": residence,
        "work_location": "nyc",
        "fica_exempt": fica_exempt,
        "treaty_exempt_income": treaty_exempt_income,
        "itemized_deductions": itemized_deductions,
        **result,
        "effective_tax_rate": result["total_tax"] / gross_income if gross_income else 0.0,
        "retirement_tax_savings": _rounded(baseline["total_tax"] - result["total_tax"]),
        "sources": list(dict.fromkeys((*_SOURCES, *SOURCES))),
        "assumptions": [
            "2026 bounded regular wage-tax estimate for a full-year federal nonresident alien; no automatic standard deduction.",
            "One employee, one full-year NYC job, only US effectively connected wage income; single or MFS; no dependents, other income, credits or AMT preferences.",
            "Full-year NYC state/city tax residency or full-year NJ residency is independently confirmed; federal NRA status does not determine state residency.",
            "FICA exemption is explicit user-confirmed eligibility, not inferred from nationality or visa. Qualifying F/J student exemptions generally involve limited calendar-year periods and authorized qualifying employment.",
            "Treaty exemption is an independently eligible wage amount, not automatically $5000. China treaty Article 20(c) covers up to $5000 personal services only for qualifying students/trainees and the reasonably necessary education/training period.",
            "NY/NYC honor eligible federal treaty wage exclusions. NJ wage law is independent of federal AGI; this model retains treaty wages in NJ and reduces the NJ credit income fraction accordingly.",
            "Itemized deductions are the final eligible federal Schedule A total after limits and exempt-income allocation; no state tax liability is automatically treated as tax paid. State standard deductions are used independently.",
            "Positive itemized deductions require nonexempt wages before 401(k) no greater than $500000 and must pass a conservative AMT screen; otherwise use manual take-home/Form 6251 review.",
            "Traditional 401(k) only, maximum $24500 and no catch-up. Tax savings hold the other user-entered deductions fixed; actual eligibility may change with AGI.",
            "No other deductions/credits except NYC school tax credits and capped NJ other-state credit; refundable school-credit excess is excluded from spendable wages. State payroll insurance and health premiums are excluded.",
            "No dual-status year, residency/treaty eligibility determination, foreign income/credits, capital income, state moves, remote-work allocation, India standard-deduction exception, or complete 1040-NR preparation.",
            "Sources checked 2026-11-02; annual gross wages $0 through $1 billion; amount components rounded to cents.",
        ],
    }
