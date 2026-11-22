Related calculators: China and US nonresident wage scenarios are implemented in `tax-china-nonresident-2026.md`; retirement contributions and withdrawals are modeled as described in `retirement-model.md`. The scope below describes this specific US-resident calculator.

# 2026 NYC employment tax planning

`pf.sim.calculators.employment_tax.calculate_employment_tax` estimates annual
tax liability and spendable wages for **one employee in a full-year NYC job**,
living all year in NYC (including LIC) or New Jersey. Sources checked **2026-11-02**.
The function is pure Python, does not access the network, and does not log inputs.
Supported annual gross wages range from $0 through $1 billion.

Supported filing statuses are `single` and `married_filing_jointly`. A joint
return assumes a nonworking spouse with the same residence. Both spouses are
under 65, not blind, not dependents, and not veterans. There are no dependents,
other income, itemized deductions, tips/overtime deductions, or other credits.
All wages, including bonus, are ordinary W-2 compensation subject to FICA.
Only employee **traditional 401(k)** deferrals are supported, up to the smaller
of wages and $24,500; there are no catch-up contributions or employer matches.

Federal and NY standard deductions apply. NJ has no standard deduction; this
model uses its $1,000 personal exemption per spouse and excludes 401(k) deferrals
from NJ wages. NJ tax is zero at gross-income filing thresholds of $10,000 single
or $20,000 joint. No NJ property-tax/rent, medical, tuition, or other deductions
or relief are assumed.

NY tax follows the **2026** rate schedule and all applicable benefit-recapture
worksheets in IT-2105-I, rather than treating the published bands as sufficient
above $107,650 NYAGI. Worksheet selection uses taxable income; recapture phase-in
uses AGI, with its prescribed four-decimal rounding. Published rounded base-tax
amounts are retained. This produces small rounding discontinuities, some genuine
worksheet-transition jumps, and the statutory cliff above $25 million NYAGI.

NYC tax includes the fixed and rate-reduction school tax credits, using AGI
eligibility cutoffs of $250,000 and $500,000. City tax is floored at zero;
`nyc_refundable_credit_excess` records any excess refundable school credit, which
is excluded from spendable wage planning. Other credits (including household,
earned income, and NYC income-tax elimination credits) are outside this scenario.

A NJ resident owes NY nonresident tax on the same NY-source wages and no NYC
resident tax. Here all income is taxable to both states before exemptions, so
the NJ-COJ income fraction is 100%. The credit is `min(NY tax, NJ tax before
credit)`: combined state liability is the larger of the two, rather than their
sum. This simplification does **not** apply to mixed-source or other income.

`net_income = gross_income - retirement_contribution - total_tax`.
`retirement_tax_savings` is the liability reduction against the same scenario
without the 401(k); it is not an additional inflow. FICA uses gross wages, not
wages after the deferral. Dollar components use cents with half-up rounding;
this is a rate-schedule planning estimate, so filed-return tax-table and
whole-dollar rounding can differ. Additional Medicare uses the actual filing
threshold ($200,000 single/$250,000 joint), not the employer's withholding
threshold. State payroll insurance/PFL, health premiums and other deductions
are excluded, so this is not a paycheck deposit prediction.

Invalid numerical inputs, excess deferrals, other filing statuses, other
residences, and other work locations raise `ValueError` with manual take-home
guidance. A deferral that leaves insufficient wages for the employee's taxes is
also rejected, rather than returning negative take-home. For moves, nonresident
aliens, dual state residency, multiple earners,
equity with different sourcing, remote-work allocation, special deductions or
credits, use the app's manual annual take-home input instead. Callers must show
the assumptions; the function cannot infer personal eligibility from wages.

## Primary sources

| Authority and direct source | Rules used |
| --- | --- |
| [IRS Rev. Proc. 2025-32, §4.01 and §4.14](https://www.irs.gov/irb/2025-45_IRB#REV-PROC-2025-32) | 2026 federal rate schedules and $16,100/$32,200 standard deductions |
| [IRS 2026 retirement limits](https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500) | $24,500 regular employee deferral limit |
| [IRS retirement contribution/FICA treatment](https://www.irs.gov/retirement-plans/retirement-plan-faqs-regarding-contributions-are-retirement-plan-contributions-subject-to-withholding-for-fica-medicare-or-federal-income-tax) | Traditional deferrals reduce income-tax wages, not Social Security/Medicare wages |
| [SSA contribution and benefit base](https://www.ssa.gov/oact/cola/cbb.html) | 2026 $184,500 base and 6.2% employee tax |
| [IRS Publication 15](https://www.irs.gov/publications/p15) and [Additional Medicare FAQ](https://www.irs.gov/businesses/small-businesses-self-employed/questions-and-answers-for-the-additional-medicare-tax) | 1.45% Medicare and 0.9% additional tax with filing-status thresholds |
| [NY IT-2105-I (2026), pp2–5, 8, 10](https://www.tax.ny.gov/pdf/2026/inc/it2105i_2026.pdf) | NY standard deductions, 2026 schedules, recapture worksheets 1–11, NYC rates and school credits |
| [NY residency/nonresident FAQ](https://www.tax.ny.gov/pit/file/nonresident-faqs.htm) | NY-source wages for nonresidents; NYC tax applies to residents |
| [NJ current rate schedules](https://www.nj.gov/treasury/taxation/taxtables.shtml) and [2020-and-after rate PDF](https://www.nj.gov/treasury/taxation/pdf/current/njtaxratesch.pdf) | Current NJ rate schedules; rates may be used below $100,000 instead of tax tables |
| [NJ personal exemptions](https://www.nj.gov/treasury/taxation/njit2.shtml) and [NJ wages](https://www.nj.gov/treasury/taxation/njit5.shtml) | $1,000 per spouse; 401(k) exclusion |
| [NJ other-jurisdiction credit](https://www.nj.gov/treasury/taxation/njit14.shtml) and [latest NJ-1040 instructions, pp3, 9, 31–34](https://www.nj.gov/treasury/taxation/pdf/current/1040i.pdf) | Filing thresholds and NJ-COJ cap/income fraction; latest return instructions available at verification were 2025, supplemented by current NJ guidance |

Run the calculator checks with:

```sh
cd backend
.venv/bin/python -m pytest tests/test_employment_tax.py -q
```
