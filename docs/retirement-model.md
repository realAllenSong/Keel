# Traditional 401(k) contributions and withdrawals in planning

The monthly Monte Carlo engine keeps liquid investments and pretax retirement investments in
separate pools. Both use the selected stock/bond/cash allocation, market model, fees, historical
window, and rebalancing frequency. Rebalancing stays within each pool; it never makes a locked
401(k) spendable or moves retirement assets into a taxable account without a withdrawal.

The model uses today's dollars throughout. Its future withdrawal tax is an explicit scenario
assumption, not a projection of future tax brackets or a retirement tax return.

## Inputs and cash-flow basis

| Scenario field | Meaning |
| --- | --- |
| `initial` | Total investable assets today, including the current pretax 401(k) balance. |
| `retirement_balance` | The part of `initial` that is pretax retirement money. It must be between zero and `initial`; it is not added to `initial` again. |
| `annual_income` | Annual spendable cash after current income/payroll taxes and employee 401(k) deductions. |
| `annual_retirement_contribution` | A separate annual traditional employee contribution, deposited monthly while working. It is fixed in real dollars and does not grow with `income_growth`. |
| `retirement_withdrawal_tax_rate` | User-entered effective tax on future gross pretax retirement withdrawals, from 0 through 1. Include the combined taxes the scenario is intended to assume. |
| `retirement_access_year` | User-entered years from now until retirement assets may fund cash needs. Zero means accessible now, and must be reviewed when enabling retirement planning. |

Employee contributions must not be deducted from `annual_income` a second time. When the
employment tax calculator supplies spendable pay and a traditional 401(k) contribution, pass
those as the two separate flows. A $60,000 spendable income and $12,000 contribution mean
$60,000 cash available before spending plus $12,000 deposited to retirement, not $48,000 cash.
Employer matching is not automatically inferred. The scenario does not enforce future legal
contribution limits, catch-up rules, or future compensation changes.

All four retirement fields default to zero. Scenarios leaving all four at zero retain the
previous single-pool simulation and its exact seeded paths, including its absorbing-zero
behavior after ruin. An existing balance is only treated as retirement money when identified
by `retirement_balance`.

## Monthly funding

Each month the engine first applies the existing market returns and fees. During working
months it deposits one twelfth of the annual retirement contribution into the retirement
pool. Contributions stop when working income stops, using the same rounded working-month
boundary as the existing engine.

Salary, spending, pension, and lifecycle events form the month's net cash flow. Pension and
event inputs retain their existing cash-flow meaning; the new retirement tax rate does not
tax those inputs. A positive cash flow is invested in the liquid pool. A negative cash flow
is funded in this order:

1. Withdraw available liquid investments pro rata across their stock/bond/cash holdings.
2. Once `month_index >= retirement_access_year * 12`, withdraw available retirement assets
   for any remaining cash need. For effective rate `r < 1`, the gross amount required is
   `remaining_cash_need / (1 - r)`. The entire gross amount leaves retirement holdings; only
   the amount after tax pays the cash need.
3. If the need still exceeds available spendable assets by more than one cent, record the
   first funding shortfall. A 100% assumed tax rate produces no spendable retirement cash,
   so the engine leaves that retirement balance invested instead of dividing by zero.

For example, start with $18,000 total assets: $6,000 liquid and $12,000 pretax retirement.
With no return, $12,000 net annual spending, immediate access, and a 25% effective retirement
withdrawal tax, liquid assets pay the first $6,000. The remaining $6,000 needs an $8,000 gross
retirement withdrawal: $2,000 tax and $6,000 spending. Year-end assets are $4,000.

Lifecycle outflows follow the same funding order and tax treatment. Lifecycle inflows go to
liquid investments. The engine nets all cash flows within each month; it does not attempt to
model the order of individual payments inside a month.

## Reading the results

Wealth percentiles, terminal values, historical replay paths, and lighthouse comparisons show
**gross assets: liquid assets plus the remaining pretax retirement balance**. They are not an
estimate of spendable after-tax liquidation value. Taxes already paid on modeled withdrawals
have reduced these assets; tax on retirement assets that remain invested has not been charged.

The success probability means every modeled cash need was funded, including assumed
retirement withdrawal tax and the access restriction. A path can fail while still owning a
large locked 401(k). In a retirement scenario, that shortfall permanently counts as failure,
but remaining retirement assets keep earning returns. Subsequent income, contributions,
events, and accessible withdrawals continue. The engine does not erase locked assets, invent
borrowing for an unfunded payment, or reset a failed path after a later windfall. Consequently,
terminal wealth alone does not indicate whether spending was fully funded.

Constant retirement spending and guardrail spending are net cash needs. Percentage spending
is the selected percentage of gross portfolio assets, also treated as a net cash need before
withdrawal tax. Guardrail tests use gross portfolio assets and then gross up the resulting net
cash need. This preserves the existing spending-rule definitions; with retirement tax, a
percentage rule can exhaust assets faster than the percentage alone suggests.

The first lighthouse's savings amount includes liquid savings plus retirement contributions.
Lighthouses remain gross-asset heuristics and do not establish liquidity or after-tax funding
sufficiency; use success probability for that. Investment drawdown and time underwater still
measure the same market-only allocation index, excluding contributions, spending, and taxes.
Initial-asset sensitivity varies both pools proportionally. Allocation comparisons, retirement
timing comparisons, and historical replays preserve the retirement assumptions and reuse the
same market engine.

## Tax and access assumptions

The IRS explains that retirement-plan distributions generally enter income except for
after-tax contributions and qualified designated Roth distributions. This model therefore
treats the identified retirement pool as fully pretax; it does not represent Roth accounts or
mixed after-tax basis. See [IRS: Tax on normal distributions](https://www.irs.gov/retirement-plans/plan-participant-employee/retirement-topics-tax-on-normal-distributions).

The IRS describes an additional tax that may apply before age 59½ and exceptions including
certain distributions following separation from service. Plan distribution rules also matter.
The app does not guess the user's age or eligibility: `retirement_access_year` is the user's
explicit assumption about when penalty-free withdrawals are available. Before that point the
model blocks retirement spending entirely; it does not simulate early-withdrawal penalties,
hardship distributions, loans, or exception strategies. See [IRS: 401(k) general distribution rules](https://www.irs.gov/retirement-plans/plan-participant-employee/401k-resource-guide-plan-participants-general-distribution-rules).

The chosen effective tax rate stays fixed across paths and years. It does not calculate
future federal/state brackets, tax on liquid investment gains or dividends, taxable Social
Security, required minimum distributions, Roth conversions, or interactions between those
items. Compare explicit rates and access dates to understand how those assumptions change
funding outcomes. Sources checked November 2, 2026.
