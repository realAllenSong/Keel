# Keel 龙骨

Local-first household finance in Chinese or English, light or dark. Four places:

- **现在 Now**: this month as a statement: spent vs a usual month, month-end forecast, left to
  spend per day, what's due next with the cash left after each, warnings, and a cash timeline
  from the 1st to past the next payday.
- **回顾 Review**: one closed month at a time: income, spending, savings, cash, net worth, the
  month's own cash timeline, 12 months of income and spending, categories against usual, net
  worth by asset class, the three lighthouses.
- **账本 Ledger**: spending, a calendar of each day plus upcoming bills and paydays, assets, and
  every transaction (categorise, edit, split, delete, or add one by hand).
- **规划 Plan**: retirement, market crashes, allocation, backtests and housing decisions.

Every chart is an exhibit: its title states the finding, values are written on the data, and
pointing at it reads out the numbers; clicking a day, month or category opens what's behind it.

All financial data stays on this machine: `raw/` and `data/` are gitignored, a pre-commit hook
refuses card/ID numbers, and tests run on synthetic fixtures only.

| | |
|---|---|
| Accounting | double-entry, SQLite (STRICT + triggers keep every transaction balanced), exact decimals |
| Import | drop statements anywhere in Keel or into `~/Finance-Inbox/`; dedup, transfer pairing, rules |
| Your own money | card ↔ 支付宝/微信 payments and top-ups, your other wallet login, C2C crypto buys on OKX are transfers, never spending (names that are you: Settings) |
| Editing | fix a line's date, amount or text, split it across categories, delete it, add cash by hand; fixes survive re-imports and rebuilds |
| Sources | Chase, Amex, BofA, Schwab, Fidelity, IBKR, 支付宝, 微信支付, 宁波银行, OKX; others by teaching a CSV/Excel layout once |
| This month | pace vs your usual month, forecast, budget and safe-to-spend, recurring bills/paydays/card payments learned from history, 35-day cash projection |
| Alerts | overspending pace, over budget, a paycheck that didn't arrive, cash below your floor, bills this week, annual renewals; macOS notifications once each |
| Reports | cash-flow statement, balance sheet, insights, three lighthouses |
| Simulation | 1871→today history, block bootstrap / Student-t, sensitivity, earliest retirement, worst starts |
| Tools | rolling-window backtests (DCA vs lump vs buy-the-dip vs MA), mortgage / rent-vs-buy / rental |
| Backups | nightly verified snapshots (7 days, 8 weeks, 12 months, yearly), optionally in iCloud Drive |
| Settings | language (system / 中文 / English), appearance (system / light / dark), budget, low-cash floor, notifications, names that are you, backups |

Design: ink and paper editorial, described in [DESIGN.md](DESIGN.md).
Keel is a native SwiftUI Mac app ([apple/README.md](apple/README.md)) with the Python ledger
engine bundled inside it ([desktop/README.md](desktop/README.md): engine, signing, updates, local
state). There is no web front end.

## Install / update on your Mac

```bash
git pull && make install    # builds Keel.app, backs up your ledger, replaces /Applications/Keel.app
```

Your data lives in `~/Library/Application Support/Keel` and is kept. After this install, Keel can
also update itself: 设置 → 版本与更新 → 检查更新 / 更新并重启
([how it works](desktop/README.md#updating)). Try it on synthetic data instead with
`make seed-demo && make dev-demo`, then `make run` in a second terminal.

## Run

```bash
make hooks            # once: pre-commit privacy + lint hooks
make app              # build dist/Keel.app (SwiftUI + bundled engine, signed)
make seed-demo        # synthetic household in data/demo.db
make dev-demo         # engine API on 127.0.0.1:8711 with the demo ledger …
make run              # … and the SwiftUI app from source against it
make api              # the engine on your real ledger (data/ledger.db)
make test             # backend + engine tests, translations, Swift unit tests
```

## Your data

```bash
cp config/accounts.example.yaml config/accounts.local.yaml        # map exports to accounts
cp config/assumptions.example.yaml config/assumptions.local.yaml  # expected returns, 4% rule …
cd backend
uv run pf import ~/Downloads/Chase1234_Activity.CSV   # or drop files into ~/Finance-Inbox
uv run pf check                                        # balance + statement reconciliation
uv run pf value Assets:Vanguard:Brokerage 2026-09-30 34656.95
uv run pf market --history                             # FRED + Shiller data for simulations
uv run pf decisions export decisions.yaml              # portable backup of your rules, fixes, valuations
uv run pf alerts                                       # notify new alerts now (the daily job does this)
uv run pf agents install                               # launchd: app always on, inbox, daily, monthly
```

Bank formats for Chase / Amex / BofA are config files in `backend/pf/importers/profiles/`; a new
bank is usually a new YAML file. 支付宝, 微信支付, 宁波银行, Schwab, Fidelity, IBKR and OKX have
dedicated parsers (`backend/pf/importers/`). Brokers and OKX are booked as one account each:
deposits, withdrawals, dividends, interest, fees and tax are cash flows; the positions / NAV export
sets the account's value. All layouts are **unverified until checked against a real export**;
synthetic exports with known ground truth live in `fixtures/brokers` (`test_brokers.py` evaluates
the whole pipeline on them and on randomly generated households).

## Language

UI text is written in Chinese and looked up for English with `L("…")` (Swift) and `tr("…")`
(engine). Add the English to `apple/Sources/KeelCore/L10nEN.swift` or `backend/pf/i18n_en.py`;
`apple/scripts/check_l10n.py` and `tests/test_i18n.py` fail on anything missing.

## For agents: `keel` (CLI), the `keel` skill, and `keel mcp`

Keel is usable by other agents on this Mac, all through the same code (`backend/pf/agent/core.py`,
which calls the app's API in-process: no server, no port, app open or closed).

- **CLI** `keel <command> [--json]`: `status`, `summary`, `transactions`, `assets`, `review`,
  `categories`, `categorize`, `add`, `import`, `sync`, `check-balance`, `plan`, `close`, `doctor`,
  `open`. Never interactive; exit 1 with `{ok:false,error,message,fix}` on stderr. `keel --help`.
  Installed to `~/.local/bin/keel` by `make install` (`scripts/install-agent.sh`); it runs the
  engine inside `/Applications/Keel.app`, or this checkout with uv when no app is installed.
- **Skill** `skills/keel/` (when and how, for Claude Code and Codex), added through Rosetta:
  `rosetta evolve skills/keel --update`.
- **MCP** `keel mcp` (stdio, 20 `keel_*` tools, one compact JSON line each) for chat apps without
  a shell. Registered for Claude Desktop in Rosetta's `registry/mcp.yaml`.

## License

Keel is free software under the [GNU Affero General Public License v3](LICENSE). Copyright ©
2026 realAllenSong. If you share Keel or a work based on it, or run it for others over a network,
keep the attribution "Based on Keel by realAllenSong" (see [NOTICE](NOTICE)) and publish your source
under the same license.
