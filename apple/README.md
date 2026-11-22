# Keel for macOS (SwiftUI)

Keel is a native SwiftUI app. The ledger, importers, reports and simulations stay in Python
and ship inside the app as a helper process called the **engine**:

```
Keel.app/Contents/
  MacOS/Keel                          SwiftUI app (this package)
  Resources/engine/keel-engine/       PyInstaller build of desktop/ledger_app/engine.py (no Qt)
  Resources/Fonts/                    Geist and Geist Mono (SIL OFL), via ATSApplicationFontsPath
  Resources/build-info.json           commit/branch it was built from (for the updater)
```

When Keel starts, it launches the engine with a pipe on stdin. The engine runs migrations, serves
the FastAPI API on a random `127.0.0.1` port, prints `KEEL_READY <url>`, and runs the inbox
watcher and the daily/monthly jobs. It exits as soon as that pipe closes, so it never outlives the
app. If it crashes, it is restarted (up to 3 times).

Every screen is native:

| Place | Screens |
|---|---|
| 每月 Monthly | original current-month figures and pace exhibit; closed months retain their statement, cash timeline, monthly and net-worth exhibits; existing spending categories/income sources are embedded without another summary; assets open in the existing account-detail sheet |
| 年度 Annual | month/year switch within 每月; exact annual totals and monthly table, month/amount drilldowns and the same asset snapshot; elapsed months and partial data coverage are explicit |
| 日历 Calendar | historical month navigation, actual daily income/spending and clickable estimated bills/paydays; transfers, valuations and opening balances are excluded from cashflow |
| Contextual details | category/amount drilldowns support editing, splitting and manual entries; individual category corrections persist without automatically rewriting other transactions; legacy detail routes remain compatible |
| 规划 Plan | Monte Carlo workspace (fan chart, drawdowns, worst historical starts, allocation search, sensitivity, saved plans) · DCA backtest · housing calculators |
| Sheets | statements to confirm (claim an account, unlock a zip, teach a CSV layout) · how to download statements |
| Everywhere | ⌘O / drag files onto the window / scan Downloads, menu bar, launch at login, 设置 |

Screens keep their last data while you move between places, so a tab you come back to shows its
numbers at once and refreshes underneath.

Package layout: `KeelCore` holds the engine process, the API client, models, formatting and
translations, and has no UI (an iPhone app can reuse it). `Keel` holds the app and its screens.
`keel-selftest` starts the real engine on a ledger and decodes every response the app uses, in
both languages.

## Build

Requires macOS 14+, Xcode command line tools (Swift 5.10+) and uv.

```sh
make app                 # dist/Keel.app (engine -> swift build -> assemble -> sign)
make install             # same, then replace /Applications/Keel.app (ledger backed up first)
cd apple && swift test   # unit tests (formatting, translations)
python3 apple/scripts/check_l10n.py
```

Signing uses the first Developer ID Application certificate in your keychain, or ad-hoc signing
if there is none (`SIGN_IDENTITY` overrides). `NOTARIZE=1` notarizes the app (see desktop/README.md).
For UI work without rebuilding the engine: `make dev-demo` (or `make api`) in one terminal and
`make run` in another (`KEEL_ENGINE_URL` points the app at that engine).

CI (`.github/workflows/macos-app.yml`) builds the bundle, runs the unit tests and the self-test on
the synthetic demo ledger, launches the app, and renders screenshots (below). It runs on macOS
runners, which a private repository bills at 10× its Actions minutes.

## Design

`Sources/Keel/Theme.swift` holds the design language: Coral in light (warm paper, cotton cards, ink,
one coral accent) and Dusk in dark (slate, periwinkle series, apricot accent); words in Geist,
every amount in Geist Mono. Run from source without the bundled fonts, the system faces stand in.

Charts are research exhibits, drawn with Canvas rather than Swift Charts so every label sits where
it reads best: a numbered title that states the finding, units in the subtitle, series named at
their ends, a right-hand value axis, hairline grids, a source line, and a hover readout (with
click-through where there is something behind the point). `Exhibits.swift` has the 现在 / 回顾
exhibits and the shared pieces (`Exhibit`, `Figs`, `Pen`, `InteractiveCanvas`);
`ExhibitCharts.swift` has the general line and bar exhibits used by 账本 and 规划.

Every CI run renders all screens (light/dark, 中文/English) from the demo ledger with
`KEEL_SNAPSHOT_DIR=<dir> Keel.app/Contents/MacOS/Keel` and pushes the PNGs to the
`ci-screenshots` branch: `git fetch origin ci-screenshots && git archive origin/ci-screenshots | tar -x -C shots`.

## Translations

Source strings are Chinese: `L("…")`, with English in `Sources/KeelCore/L10nEN.swift`.
`scripts/check_l10n.py` fails on a missing or unused entry, mismatched `{placeholders}`, and
Chinese outside `L()` (mark intentional cases with `// l10n-allow`).

Planning keeps the existing charts and saved values. New plans require recurring cash-flow review (or explicit manual assumptions); the category sheet shows every observed category, including unchecked wires and tuition, and lets the user edit expected annual amounts. Partial salary history is never annualized. The LIC/NJ comparison estimates 2026 full-year US-resident single-job W-2 take-home, with NY recapture, FICA and capped NJ credit; applying a scenario is explicit. The same sheet now also supports China resident annual salary in CNY with explicit planning FX, and US nonresident wage scenarios with explicit treaty/FICA/deduction inputs. More complex returns require separate review. See `docs/tax-calculation-2026.md` for sources and boundaries. The portfolio simulation remains in real dollars, now tracks future 401(k) deposits and liquid versus pretax balances, and grosses up accessible retirement withdrawals at an explicitly confirmed effective tax rate. Taxable-account investment taxes and home/vehicle equity remain outside the model. See `docs/retirement-model.md`.

Mailbox has a Statement ZIP passwords manager and a direct Pending statements entry. Saved codes stay in macOS Keychain; new downloads and existing pending archives retry up to five recent provider codes, retaining files on failure. Manual unlock offers an explicit remember option. Codes may change with each export; mailbox pickup cannot request exports. Official consumer export paths and agent-payment scope: `docs/china-data-access-research-2026-11-02.md`.
