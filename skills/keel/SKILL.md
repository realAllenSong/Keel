---
name: keel
description: Read and change the user's Keel 龙骨 personal-finance ledger on this Mac with the `keel` command — spending, income, net worth, account balances, transactions and their categories, statement imports, bank sync, monthly close and the financial plan. Use when the user asks how much they spent or earned, where the money went, what they are worth, whether cash will last, wants a transaction found, categorised or recorded, a statement imported, accounts synced, or mentions Keel, 龙骨, 记账, 账本, 账单, 花销, 支出, 收入, 净资产, 资产, 分类, 对账, 结账, 财务规划, 预算. Prefer it over reading bank files or the SQLite ledger by hand.
---

# Keel 龙骨 (personal finance ledger)

The user's own app at `/Applications/Keel.app`: a double-entry ledger of their banks, cards,
brokers, Alipay and WeChat, in US dollars and Chinese yuan. Everything is local. `keel` runs
whether the app is open or not.

## Pick the interface

- **Shell (main path):** `keel <command> … --json`. One JSON object on stdout (`"ok": true`);
  on failure exit code 1 and `{"ok": false, "error", "message", "fix"}` on stderr. Without
  `--json` the output is plain text for a person. Never interactive. `keel <command> --help`.
- **No shell (chat apps):** the same commands are MCP tools named `keel_*` (`keel mcp`).

## Tasks

| The user wants | Command | MCP tool |
|---|---|---|
| Is the ledger current, what needs doing | `keel status` | `keel_status` |
| Earned / spent / saved, top categories | `keel summary [--month YYYY-MM \| --year YYYY]` | `keel_summary` |
| Find or list transactions | `keel transactions [--month] [--category Expenses:Food] [--account] [--query text] [--pending] [--limit N]` | `keel_transactions` |
| Net worth, every account's balance | `keel assets [--date YYYY-MM-DD] [--currency USD\|CNY]` | `keel_assets` |
| Stocks, funds, options held: value, cost, gain | `keel holdings [--date YYYY-MM-DD]` | `keel_holdings` |
| Buys, sells, splits, option events | `keel trades [--from D] [--to D] [--symbol NVDA] [--cash]` | `keel_trades` |
| How the investing has gone: overall gain and annual rate against the same money in QQQ and SPY, dividends, realised gains sale by sale, a short/long-term and wash-sale tax view (estimate) | `keel gains [--year 2026]` | `keel_gains` |
| Brokerage accounts + holdings + trades for a period (an employer's account certification) | `keel brokerage-report --from D --to D` | `keel_brokerage_report` |
| Subscriptions and fixed bills: amount, day, next date, price rises, total per month | `keel bills [--dismiss KEY \| --restore KEY]` | `keel_bills` |
| Group lines across categories (a trip, a move) and total them | `keel tags [ID… --tag NAME [--remove]]`, `keel tags --rule MERCHANT --tag NAME` (that merchant from now on too), then `keel transactions --tag NAME` | `keel_tags` |
| Savings goals: saved, pace, when reached, what a date needs | `keel goals [--add NAME --target N [--by D] [--account A]…] [--delete ID]` | `keel_goals` |
| What is waiting for a category | `keel review` | `keel_review` |
| Valid categories and accounts | `keel categories` | `keel_categories` |
| File transactions under a category | `keel categorize ID… --category X [--rule merchant]` | `keel_categorize` |
| Record cash or a payback | `keel add --date --amount --category --account --description` | `keel_add_transaction` |
| Import statement files | `keel import FILE… [--account NAME]` | `keel_import` |
| A statement that is a PDF or a screenshot | transcribe it into a Keel statement file (below), then `keel import` | `keel_import` |
| Pull from connected banks now | `keel sync [plaid\|snaptrade\|okx\|mailbox\|ibkr_flex]` | `keel_sync` |
| Set an account's real balance | `keel check-balance --account --amount [--date]` | `keel_check_balance` |
| Cash low point, net worth in 1/3/5 years | `keel plan` | `keel_plan` |
| Close last month | `keel close [--month] [--yes]` | `keel_close_month` |
| Installed and reachable? | `keel doctor`; `keel open` opens the app | |

## What to know

- **Names.** Categories and accounts are colon paths: `Expenses:Food:Dining`,
  `Income:Salary`, `Assets:Chase:Checking-0102`, `Liabilities:Chase:Card-0003`. Use
  `keel categories` instead of guessing. A category filter is a prefix (`Expenses:Food`).
- **Signs.** A transaction's `amount` has its account's sign: money out is negative. In
  summaries, `spending` and `income` are positive.
- **Currencies.** Lines keep their own currency (USD or CNY). Summaries and assets convert to
  `--currency` (default USD); `missing_fx` lists what could not be converted.
- **Transfers are not spending.** Card payments and moves between the user's own accounts are
  paired automatically. Money sent to an account Keel does not track goes to
  `Assets:Elsewhere`, not to an expense.
- **`--rule`** on `categorize` files that merchant the same way from now on. Give a distinctive
  merchant word (`sweetgreen`, `anthropic`), never a generic one (`transfer`, `payment`).
- **This month** is in progress: `summary` without `--month` covers the 1st to today.
- **The plan** is the one saved in the app (life stages, events); without one, `keel plan`
  uses the ledger's own income and spending.

## A statement no importer reads (PDF, screenshot)

Read the document yourself, write its lines as a Keel statement file, and import that. The lines
then go through the ordinary pipeline: no duplicates on a second import, transfers paired,
categories suggested.

```
# keel statement
# institution: Chase
# kind: card            (bank | card; default bank)
# account: 0003         (last digits, optional)
# currency: USD
date,description,amount,balance
2026-10-02,COFFEE SHOP,-4.50,-104.50
2026-10-05,PAYMENT THANK YOU,104.50,0.00
```

- Dates are YYYY-MM-DD. Amounts are in ledger sign for that account: money into a bank account is
  positive; a card purchase is negative and a card's debt is a negative balance.
- Include `balance` (the balance after each line) whenever the statement prints it. Keel then
  compares its own total with the statement's, and the result lists `balance_differences` when a
  figure was misread. Fix the file and import again until there is none.
- Copy every line of the period, in order. Never invent, merge or skip lines.

## Etiquette

- Read freely. **Change only what the user asked for**: categorising, adding, importing,
  syncing, setting a balance and closing a month all write to their real ledger.
- When the user delegates judgement ("你自己判断"), categorise confidently, then report what
  you filed where in a short summary; ask only about large or truly ambiguous amounts.
- This is private financial data: quote only what answers the question, and never paste
  ledger contents into external services, issues or commits.
- Keys and passwords are set in the app (设置 › 高级). Never ask for them or pass them as
  arguments.
- One `keel sync` at a time; it can take a minute.
