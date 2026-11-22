# China resident and US nonresident wage planning, 2026

Primary sources checked **2026-11-02**. These are separate pure calculations:
they neither determine tax residency nor prepare a return. They do not use
nationality, immigration documents, or other private identifiers.

## China resident salary

`calculate_china_employment_tax(gross_income_cny, social_insurance_annual=0,
housing_fund_annual=0, special_deductions_annual=0, other_deductions_annual=0)`
returns CNY amounts. It applies the annual ¥60,000 basic deduction and the
seven comprehensive-income rates, from 3% through 45%, to salary income.

Social-insurance and housing-fund inputs are **actual eligible employee annual
contributions** within applicable local rules and limits. The calculator does
not invent a city rate or contribution base. These amounts reduce both taxable
income and spendable wages. Special additional and other eligible deductions
reduce taxable income only; subtracting them from cash again would count their
cost twice. Employer contributions are not employee take-home deductions.

The scenario is full-year China-resident salary only, including bonus combined
with comprehensive income. The optional separate annual-bonus election,
other comprehensive income, business income, subsidies, foreign tax credits,
and cross-border double taxation are excluded. Inputs must be finite,
nonnegative and at most ¥1 billion each; employee contributions cannot exceed
wages. Deductions floor taxable income at zero without generating a tax refund.

Sources: [State Taxation Administration: Individual Income Tax Law, articles
1–3 and 6, and comprehensive-income rate table](https://www.chinatax.gov.cn/n810219/n810744/n3752930/n3752974/c3970366/content.html),
and [Liaoning tax authority rate/quick-deduction calculator](https://etax.liaoning.chinatax.gov.cn/sword?ctrl=SBSfjsqCtrl_initViewGrsdsJsq).

## US federal nonresident, NYC job

`calculate_nonresident_employment_tax(gross_income,
retirement_contribution=0, residence='nyc', fica_exempt=False,
filing_status='single', treaty_exempt_income=0, itemized_deductions=0)`
returns the US resident calculator's common tax/take-home keys plus USD,
`tax_context='us_nonresident'`, federal AGI/taxable income, explicit eligibility
inputs, and the NJ credit income fraction. Supported federal filing statuses
are `single` and `married_filing_separately`, with the distinct 2026 MFS top
bracket starting above $384,350. There is **no automatic federal standard
deduction**. The India student treaty exception is outside this scenario.

Federal NRA status is independent of state residency: the scenario assumes
full-year state/city residency in NYC or full-year NJ residency, independently
confirmed by the user, and one full-year NYC-source wage. There are no other
income sources, dependents, tax credits, or AMT preferences. NY standard
deduction and NYC school credits follow the existing 2026 state helpers; NJ
uses its independent wage tax base and $1,000 single/MFS exemption.

`fica_exempt` is explicit confirmation of an independently established
exemption on this wage. A visa name does not establish eligibility. Qualifying
F/J student exemptions generally depend on limited **calendar-year** periods,
federal tax residency, and authorized employment serving the admission purpose;
researchers and other visa categories have different rules. Otherwise FICA
applies to gross salary, including amounts deferred into a 401(k) or excluded
from federal income tax by treaty. Additional Medicare uses $200,000 single or
$125,000 MFS, rather than the employer's $200,000 withholding threshold.

`treaty_exempt_income` is a user-confirmed eligible wage exclusion, initially
zero. It is separate from retirement deferrals and their sum cannot exceed
wages. China treaty Article 20(c) permits **up to $5,000 per tax year** of
personal-service income for an eligible student/apprentice/trainee, for the
reasonably necessary education/training period. This is not a blanket $5,000
deduction for every Chinese national or F-1 holder, nor does it establish a
FICA exemption.

NY's current NYS-50 §3-O expressly honors eligible federal treaty wage
exemptions for NYS/NYC. NJ wage law is independent of federal AGI. The model's
NJ treatment is derived from the NJ authority's inclusion of all employee
wages, its listed permitted exclusions, and the treaty's Article 2 limitation
to federal US income taxes: treaty wages remain in NJ wages. Unlike the
resident calculator's 100% same-income fraction, the NJ-COJ fraction becomes
`(gross - 401k - treaty_exemption) / (gross - 401k)`. The NJ credit is the lesser
of NY tax or NJ pre-credit tax multiplied by that fraction. This prevents a
credit on income not taxed by NY; mixed-source jobs remain outside this model.

`itemized_deductions` is the **final allowable federal Schedule A total**,
entered after deduction-specific limits, 2026 limitations, and allocation to
nonexempt effectively connected income. NRAs may deduct qualifying US state
and local income taxes **actually paid or withheld during the year**. Tax
liability calculated here is not proof of payment, so no SALT deduction is
added automatically. A qualified Schedule A input may also include eligible
US charitable contributions after their applicable limits/floor. The model
does not infer the total from its components or certify eligibility. This
deduction reduces federal taxable income, not federal AGI, NY/NJ wage bases,
or cash a second time. Retirement tax savings hold that entered total fixed;
actual deduction eligibility should be recalculated when AGI changes.

An arbitrary Schedule A total cannot reveal its AMT addbacks. To avoid an
incorrect full-return claim, positive itemized deductions are supported only
when nonexempt wages **before 401(k) do not exceed $500,000** and a conservative
AMT screen passes. The screen adds back every entered deduction and applies
the 2026 single/MFS AMT exemption and rate thresholds. If even that upper-bound
tentative minimum tax does not exceed regular tax, regular tax suffices within
the wage-only assumptions. Otherwise the function rejects the scenario with
manual-take-home/Form 6251 guidance; actual deductible-for-AMT contributions
could produce a different answer. It does not charge a guessed AMT amount.

Wages range from $0 to $1 billion; deferrals are limited to $24,500 without
catch-up and must leave enough wages for employee taxes. Other unsupported
cases also raise `ValueError` with manual guidance. Spendable income excludes
401(k) deferrals and excess refundable NYC school credits, as in the resident
calculator. State payroll insurance, insurance premiums, dual-status years,
foreign income/credits, investment income, residency elections, state moves,
and remote-work allocation are excluded.

| Primary source | Application |
| --- | --- |
| [IRS Form 1040-NR instructions, line 12 and Schedule A](https://www.irs.gov/instructions/i1040nr) and [Publication 519](https://www.irs.gov/publications/p519) | No general standard deduction; graduated ECI rates; paid/withheld state/local income-tax deductions and allocation to taxable income |
| [IRS Rev. Proc. 2025-32](https://www.irs.gov/irb/2025-45_IRB#REV-PROC-2025-32) | 2026 single/MFS regular schedules and AMT exemption/rate thresholds |
| [IRS Form 6251 instructions](https://www.irs.gov/instructions/i6251) | Schedule A tax addbacks for AMT; conservative rejection instead of assuming no minimum tax |
| [US–China treaty, articles 2 and 20](https://www.irs.gov/pub/irs-trty/china.pdf) | Covered federal taxes and conditional $5,000 student/trainee personal-services exemption |
| [IRS foreign-student FICA rules](https://www.irs.gov/individuals/international-taxpayers/foreign-student-liability-for-social-security-and-medicare-taxes) | Conditional FICA exemption and calendar-year/residency limitations |
| [NY NYS-50 §3-O](https://www.tax.ny.gov/forms/publications/wt/nys50.htm) and [TSB-A-10(7)I](https://www.tax.ny.gov/pdf/advisory_opinions/income/a10_7i.pdf) | NY treaty exemption and federal 1040-NR AGI conformity despite independent NYC statutory residency; advisory is fact-specific |
| [NJ wages](https://www.nj.gov/treasury/taxation/njit5.shtml), [NJ-1040 instructions, Schedule NJ-COJ](https://www.nj.gov/treasury/taxation/pdf/current/1040i.pdf), and [NJ credit guidance](https://www.nj.gov/treasury/taxation/njit14.shtml) | Independent NJ wage base; credit numerator includes only income taxed by both states before personal exemptions |

Current official 2026 annual NY and NJ rate sources and shared-helper details
are in [tax-calculation-2026.md](tax-calculation-2026.md). The current IRS
1040-NR/6251 instructions available during research were 2025; the explicit
2026 regular/AMT amounts come from Rev. Proc. 2025-32, rather than reusing 2025
amounts or treating draft forms as final return instructions.

Focused verification:

```sh
cd backend
.venv/bin/python -m pytest tests/test_china_tax.py tests/test_nonresident_tax.py tests/test_employment_tax.py -q
```
