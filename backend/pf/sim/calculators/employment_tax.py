"""2026 annual W-2 planning estimate for one NYC employee living in NYC or NJ.

Uses published annual rate schedules, including NY's AGI-based benefit
recapture. This is liability planning, not payroll withholding or a tax return.
See docs/tax-calculation-2026.md for supported assumptions and primary sources.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

TAX_YEAR = 2026
RETIREMENT_LIMIT = 24_500
MAX_GROSS_INCOME = 1_000_000_000
_JOINT = "married_filing_jointly"

SOURCES = (
    "https://www.irs.gov/irb/2025-45_IRB#REV-PROC-2025-32",
    "https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
    "https://www.irs.gov/retirement-plans/retirement-plan-faqs-regarding-contributions-are-retirement-plan-contributions-subject-to-withholding-for-fica-medicare-or-federal-income-tax",
    "https://www.ssa.gov/oact/cola/cbb.html",
    "https://www.irs.gov/publications/p15",
    "https://www.irs.gov/businesses/small-businesses-self-employed/questions-and-answers-for-the-additional-medicare-tax",
    "https://www.tax.ny.gov/pdf/2026/inc/it2105i_2026.pdf",
    "https://www.tax.ny.gov/pit/file/nonresident-faqs.htm",
    "https://www.nj.gov/treasury/taxation/taxtables.shtml",
    "https://www.nj.gov/treasury/taxation/pdf/current/njtaxratesch.pdf",
    "https://www.nj.gov/treasury/taxation/njit2.shtml",
    "https://www.nj.gov/treasury/taxation/njit5.shtml",
    "https://www.nj.gov/treasury/taxation/njit14.shtml",
    "https://www.nj.gov/treasury/taxation/pdf/current/1040i.pdf",
)

# Rows: (lower taxable-income threshold, published base tax, marginal rate).
# NY publishes rounded dollar bases; accumulating unrounded marginal bands
# would subtly change its published schedules and recapture worksheets.
_FEDERAL = {
    "single": (
        (0, 0, 0.10),
        (12_400, 1_240, 0.12),
        (50_400, 5_800, 0.22),
        (105_700, 17_966, 0.24),
        (201_775, 41_024, 0.32),
        (256_225, 58_448, 0.35),
        (640_600, 192_979.25, 0.37),
    ),
    _JOINT: (
        (0, 0, 0.10),
        (24_800, 2_480, 0.12),
        (100_800, 11_600, 0.22),
        (211_400, 35_932, 0.24),
        (403_550, 82_048, 0.32),
        (512_450, 116_896, 0.35),
        (768_700, 206_583.50, 0.37),
    ),
}
_NY = {
    "single": (
        (0, 0, 0.039),
        (8_500, 332, 0.044),
        (11_700, 473, 0.0515),
        (13_900, 586, 0.054),
        (80_650, 4_191, 0.059),
        (215_400, 12_141, 0.0685),
        (1_077_550, 71_198, 0.0965),
        (5_000_000, 449_714, 0.103),
        (25_000_000, 2_509_714, 0.109),
    ),
    _JOINT: (
        (0, 0, 0.039),
        (17_150, 669, 0.044),
        (23_600, 953, 0.0515),
        (27_900, 1_174, 0.054),
        (161_550, 8_391, 0.059),
        (323_200, 17_928, 0.0685),
        (2_155_350, 143_430, 0.0965),
        (5_000_000, 417_939, 0.103),
        (25_000_000, 2_477_939, 0.109),
    ),
}
# (taxable-income upper limit, AGI phase-in start, recapture base, increment).
# IT-2105-I (2026) worksheets 2-5 and 8-10; first and >$25m worksheets
# are implemented separately below.
_NY_RECAPTURE = {
    "single": (
        (1_077_550, 215_400, 567, 2_047),
        (5_000_000, 1_077_550, 2_614, 30_172),
        (25_000_000, 5_000_000, 32_786, 32_500),
    ),
    _JOINT: (
        (323_200, 161_550, 333, 807),
        (2_155_350, 323_200, 1_140, 3_071),
        (5_000_000, 2_155_350, 4_211, 60_350),
        (25_000_000, 5_000_000, 64_561, 32_500),
    ),
}
_NYC = {
    "single": (
        (0, 0, 0.03078),
        (12_000, 369, 0.03762),
        (25_000, 858, 0.03819),
        (50_000, 1_813, 0.03876),
    ),
    _JOINT: (
        (0, 0, 0.03078),
        (21_600, 665, 0.03762),
        (45_000, 1_545, 0.03819),
        (90_000, 3_264, 0.03876),
    ),
}
# NJ schedules multiply all taxable income then subtract a constant. Keeping
# that form preserves its published half-dollar rounding at $70,000 joint.
_NJ = {
    "single": (
        (0, 0, 0.014),
        (20_000, -70, 0.0175),
        (35_000, -682.50, 0.035),
        (40_000, -1_492.50, 0.05525),
        (75_000, -2_126.25, 0.0637),
        (500_000, -15_126.25, 0.0897),
        (1_000_000, -32_926.25, 0.1075),
    ),
    _JOINT: (
        (0, 0, 0.014),
        (20_000, -70, 0.0175),
        (50_000, -420, 0.0245),
        (70_000, -1_154.50, 0.035),
        (80_000, -2_775, 0.05525),
        (150_000, -4_042.50, 0.0637),
        (500_000, -17_042.50, 0.0897),
        (1_000_000, -34_842.50, 0.1075),
    ),
}


def _rounded(value: float, places: int = 2) -> float:
    return float(Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))


def _schedule(income: float, rows: tuple, *, nj: bool = False) -> float:
    for lower, base, rate in reversed(rows):
        # Published schedules say "over", not "at least". At an exact
        # threshold the lower row applies (NY/NJ bases have rounding).
        if income > lower:
            return base + rate * (income if nj else income - lower)
    return 0.0


def _ny_tax(agi: float, taxable: float, status: str) -> float:
    ordinary = _schedule(taxable, _NY[status])
    if agi <= 107_650:
        return ordinary
    if agi > 25_000_000:
        return taxable * 0.109
    first_limit, first_rate = (161_550, 0.054) if status == _JOINT else (215_400, 0.059)
    if taxable <= first_limit:
        target = taxable * first_rate
        fraction = _rounded(min(1.0, (agi - 107_650) / 50_000), 4)
        return ordinary + (target - ordinary) * fraction
    for limit, phase_start, base, increment in _NY_RECAPTURE[status]:
        if taxable <= limit:
            fraction = _rounded(min(1.0, (agi - phase_start) / 50_000), 4)
            return ordinary + base + increment * fraction
    raise AssertionError("NY worksheet range must cover all supported income")


def _income_taxes(wages: float, contribution: float, status: str, residence: str) -> dict:
    agi = wages - contribution
    joint = status == _JOINT
    federal_taxable = max(0.0, agi - (32_200 if joint else 16_100))
    ny_taxable = max(0.0, agi - (16_050 if joint else 8_000))
    federal = _rounded(_schedule(federal_taxable, _FEDERAL[status]))
    ny = _rounded(_ny_tax(agi, ny_taxable, status))
    city_before = city_credit = city = credit_excess = 0.0
    nj_before = nj_credit = nj = 0.0
    if residence == "nyc":
        city_before = _rounded(_schedule(ny_taxable, _NYC[status]))
        fixed_credit = (125 if joint else 63) if agi <= 250_000 else 0
        rate_credit = 0.0
        if agi <= 500_000:
            threshold, base = (21_600, 37) if joint else (12_000, 21)
            rate_credit = (
                0.00171 * ny_taxable
                if ny_taxable <= threshold
                else base + 0.00228 * (ny_taxable - threshold)
            )
        city_credit = _rounded(fixed_credit + rate_credit)
        city = _rounded(max(0.0, city_before - city_credit))
        credit_excess = _rounded(max(0.0, city_credit - city_before))
    else:
        # NJ has no standard deduction. One $1,000 regular exemption per
        # spouse; traditional 401(k) deferrals are excluded from NJ wages.
        if agi > (20_000 if joint else 10_000):
            nj_taxable = max(0.0, agi - (2_000 if joint else 1_000))
            nj_before = _rounded(max(0.0, _schedule(nj_taxable, _NJ[status], nj=True)))
        # All income is the same NY-source wage: NJ-COJ lines 1/2 = 100%.
        # The NY tax is the liability on that wage, not payroll withholding.
        nj_credit = min(nj_before, ny)
        nj = _rounded(nj_before - nj_credit)
    return {
        "federal_tax": federal,
        "ny_state_tax": ny,
        "nyc_tax_before_credits": city_before,
        "nyc_school_tax_credit": city_credit,
        "nyc_refundable_credit_excess": credit_excess,
        "nyc_tax": city,
        "nj_tax_before_credit": nj_before,
        "nj_credit": nj_credit,
        "nj_tax": nj,
    }


def calculate_employment_tax(
    gross_income: float,
    retirement_contribution: float = 0,
    filing_status: str = "single",
    residence: str = "nyc",
    *,
    work_location: str = "nyc",
) -> dict:
    """Estimate 2026 take-home after taxes and an employee traditional 401(k).

    Exactly one employee, one full-year NYC job, no other income, and either
    single or a joint return with a nonworking spouse. All wages (including
    bonus) must be ordinary W-2 compensation subject to Social Security and
    Medicare. Unsupported personal circumstances require manual take-home.
    """
    fallback = " Use manual annual take-home for unsupported circumstances."
    for name, value in (
        ("gross_income", gross_income),
        ("retirement_contribution", retirement_contribution),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a finite nonnegative number." + fallback)
        # Bound before converting to float (math.isfinite can overflow on
        # extremely large Python ints). The same bound is harmless for deferrals.
        if value < 0 or value > MAX_GROSS_INCOME or not math.isfinite(value):
            raise ValueError(f"{name} must be finite and between $0 and $1 billion." + fallback)
    if retirement_contribution > min(RETIREMENT_LIMIT, gross_income):
        raise ValueError(
            "Traditional 401(k) deferral exceeds wages or the 2026 $24,500 limit." + fallback
        )
    if filing_status not in ("single", _JOINT):
        raise ValueError(
            "Supported filing statuses are single and married_filing_jointly." + fallback
        )
    if residence not in ("nyc", "nj") or work_location != "nyc":
        raise ValueError("Supported residences are nyc and nj, with work_location nyc." + fallback)

    taxes = _income_taxes(gross_income, retirement_contribution, filing_status, residence)
    social_security = _rounded(min(gross_income, 184_500) * 0.062)
    medicare = _rounded(gross_income * 0.0145)
    threshold = 250_000 if filing_status == _JOINT else 200_000
    additional = _rounded(max(0.0, gross_income - threshold) * 0.009)
    income_tax = taxes["federal_tax"] + taxes["ny_state_tax"] + taxes["nyc_tax"] + taxes["nj_tax"]
    total = _rounded(income_tax + social_security + medicare + additional)
    net_income = _rounded(gross_income - retirement_contribution - total)
    if net_income < 0:
        raise ValueError(
            "Traditional 401(k) deferral leaves insufficient wages for employee taxes." + fallback
        )
    baseline = _income_taxes(gross_income, 0, filing_status, residence)
    baseline_income_tax = sum(
        baseline[key] for key in ("federal_tax", "ny_state_tax", "nyc_tax", "nj_tax")
    )
    return {
        "tax_year": TAX_YEAR,
        "gross_income": gross_income,
        "retirement_contribution": retirement_contribution,
        "filing_status": filing_status,
        "residence": residence,
        "work_location": work_location,
        **taxes,
        "social_security": social_security,
        "medicare": medicare,
        "additional_medicare": additional,
        "total_tax": total,
        "net_income": net_income,
        "effective_tax_rate": total / gross_income if gross_income else 0.0,
        "retirement_tax_savings": _rounded(baseline_income_tax - income_tax),
        "assumptions": [
            "2026 annual liability estimate using rate schedules; not paycheck withholding or a filed return.",
            "Full-year US tax resident, under 65, not blind, not a dependent or veteran; no dependents.",
            "Exactly one W-2 employee with a full-year NYC job; joint filing assumes a nonworking spouse with the same residence.",
            "All wages including bonus are NY-source ordinary compensation subject to employee FICA; no special tax-exempt wages.",
            "Traditional employee 401(k) only, no catch-up or employer match; no other pretax benefits or retirement plans.",
            "Federal/NY standard deductions; NJ regular personal exemptions and no standard deduction.",
            "No other income, itemization, deductions or credits except NYC school credits and NJ credit for NY tax on the same wages.",
            "NYC school credits reduce NYC tax to zero; refundable excess is reported separately and excluded from spendable wages.",
            "Excludes state employee payroll insurance/PFL deductions, health premiums, NJ property-tax/rent relief and other payroll deductions.",
            "Use manual annual take-home for other circumstances, moving during the year, nonresident alien status, multiple jobs, or remote-work allocation.",
            "Primary sources checked 2026-11-02; dollar components rounded to cents, NY recapture ratios to four decimals.",
            "Gross wages supported from $0 to $1 billion; deferrals must leave enough wages to pay employee taxes.",
        ],
        "sources": list(SOURCES),
    }
