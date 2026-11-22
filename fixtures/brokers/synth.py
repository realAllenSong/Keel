"""SYNTHETIC broker / bank / exchange exports with known ground truth (fake people, fake numbers).

Writers emit each institution's export layout as the parsers expect it (Schwab, Fidelity, IBKR,
宁波银行, OKX, plus the Chase checking account the money comes from). `household(seed)` builds one
randomised household across all of them and returns the files together with what a correct
import must produce, so tests can evaluate the whole pipeline instead of single rows:

    uv run python ../fixtures/brokers/generate.py   # from backend/: rewrites fixtures/brokers/*

Layout notes (unverified against real exports until a sample is checked):
- Schwab History: "Date","Action","Symbol","Description","Quantity","Price","Fees & Comm",
  "Amount"; amounts "$1,234.56" / "-$1,234.56"; a date may read "10/01/2026 as of 09/30/2026".
  The legacy layout adds a "Transactions for account ...234" title and a "Transactions Total" row.
- Schwab Positions: title "Positions for account Individual ...234 as of 04:00 PM ET, YYYY/MM/DD",
  one table, "Cash & Cash Investments" and "Account Total" rows.
- Fidelity Activity: two blank lines, header with Run Date / Account / Account Number / Action /
  ... / Amount ($), disclaimer footer and "Date downloaded".
- Fidelity Positions: Account Number / Account Name / Symbol / ... / Current Value, money market
  "SPAXX**" and "Pending Activity" rows, "Date downloaded Sep-30-2026 ..." footer.
- IBKR Activity Statement CSV: "<Section>,Header|Data|Total,..." for Statement, Account
  Information, Net Asset Value, Trades, Deposits & Withdrawals, Dividends, Withholding Tax,
  Interest, Fees, Base Currency Exchange Rate.
- 宁波银行 交易明细: preamble (户名 / 账号 6228 **** **** 1234 / 币种 / 起止日期), then 交易日期 /
  摘要 / 收入金额 / 支出金额 / 账户余额 / 对方户名 ...; the CSV variant (GBK) uses one signed
  交易金额 with 借贷标志.
- OKX: funding bills (Bill ID, Time, Type, Amount, Before Balance, After Balance, Fee, Currency)
  and an asset snapshot (Snapshot time line, Currency, Total, Value (USD)).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import random
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

D = Decimal
ZERO = Decimal(0)
CENT = D("0.01")


def usd(v: Decimal) -> str:
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _csv(rows: list[list[str]], quote_all: bool = False) -> str:
    buf = io.StringIO()
    w = csv.writer(
        buf, quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL, lineterminator="\n"
    )
    w.writerows(rows)
    return buf.getvalue()


# ---------------------------------------------------------------- ground truth
@dataclass
class Truth:
    """What a correct import must produce for one ledger account."""

    account: str  # the account the test claims the first file into
    importer: str
    dividends: Decimal = ZERO
    interest: Decimal = ZERO
    fees: Decimal = ZERO  # positive = paid
    taxes: Decimal = ZERO  # positive = paid
    transfers_in: Decimal = ZERO  # net money moved in from outside (deposits - withdrawals)
    marks: dict[str, Decimal] = field(default_factory=dict)  # date -> account value
    closing: tuple[str, Decimal] | None = None  # bank: last statement balance
    paired: int = 0  # transfers that must pair with the Chase side
    unpaired: int = 0  # transfer lines left for review (no other side imported)

    def as_json(self) -> dict[str, object]:
        return {
            "account": self.account,
            "importer": self.importer,
            "dividends": str(self.dividends),
            "interest": str(self.interest),
            "fees": str(self.fees),
            "taxes": str(self.taxes),
            "transfers_in": str(self.transfers_in),
            "marks": {k: str(v) for k, v in self.marks.items()},
            "closing": [self.closing[0], str(self.closing[1])] if self.closing else None,
            "paired": self.paired,
            "unpaired": self.unpaired,
        }


@dataclass
class Bank:
    """Chase checking: the other side of every broker deposit."""

    lines: list[tuple[dt.date, str, Decimal]] = field(default_factory=list)

    def add(self, date: dt.date, desc: str, amount: Decimal) -> None:
        self.lines.append((date, desc, amount))

    def write(self, path: Path, opening: Decimal) -> None:
        bal = opening
        out = []
        for i, (d, desc, amt) in enumerate(sorted(self.lines, key=lambda x: x[0])):
            bal += amt
            out.append(
                [
                    "CREDIT" if amt > 0 else "DEBIT",
                    d.strftime("%m/%d/%Y"),
                    desc,
                    f"{amt:.2f}",
                    "ACH_CREDIT" if amt > 0 else "ACH_DEBIT",
                    f"{bal:.2f}",
                    "",
                    i,
                ]
            )
        rows = [
            [
                "Details",
                "Posting Date",
                "Description",
                "Amount",
                "Type",
                "Balance",
                "Check or Slip #",
            ]
        ]
        rows += [r[:7] for r in sorted(out, key=lambda r: -int(r[7]))]  # newest first
        path.write_text(_csv(rows), encoding="utf-8")


# ---------------------------------------------------------------- portfolio model
@dataclass
class Book:
    """A toy portfolio: cash plus share counts, priced by a random walk."""

    rng: random.Random
    symbols: dict[str, tuple[str, Decimal]]  # symbol -> (description, start price)
    cash: Decimal = ZERO
    shares: dict[str, Decimal] = field(default_factory=lambda: defaultdict(D))

    def price(self, symbol: str, date: dt.date) -> Decimal:
        _, p0 = self.symbols[symbol]
        r = random.Random(f"{symbol}-{date.isoformat()}")
        drift = D(1) + D(str(round((date.toordinal() % 365) / 3650 + r.uniform(-0.02, 0.02), 4)))
        return (p0 * drift).quantize(CENT)

    def value(self, date: dt.date) -> Decimal:
        return self.cash + sum(
            (q * self.price(s, date)).quantize(CENT) for s, q in self.shares.items() if q
        )


def month_days(start: dt.date, end: dt.date, day: int) -> list[dt.date]:
    out, d = [], start.replace(day=1)
    while d <= end:
        x = d.replace(day=min(day, 28))
        if start <= x <= end:
            out.append(x)
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


# ---------------------------------------------------------------- Schwab
SCHWAB_HEADER = [
    "Date",
    "Action",
    "Symbol",
    "Description",
    "Quantity",
    "Price",
    "Fees & Comm",
    "Amount",
]
SCHWAB_POS_HEADER = [
    "Symbol",
    "Description",
    "Qty (Quantity)",
    "Price",
    "Price Chng $ (Price Change $)",
    "Price Chng % (Price Change %)",
    "Mkt Val (Market Value)",
    "Day Chng $ (Day Change $)",
    "Cost Basis",
    "Gain $ (Gain/Loss $)",
    "% of Acct (% of Account)",
    "Security Type",
]


def schwab(
    rng: random.Random, out: Path, bank: Bank, start: dt.date, end: dt.date, legacy: bool
) -> Truth:
    t = Truth("Assets:Schwab:Brokerage-234", "schwab")
    book = Book(
        rng,
        {"VTI": ("VANGUARD TOTAL STOCK MARKET ETF", D("280")), "NESN": ("NESTLE SA ADR", D("95"))},
    )
    rows: list[list[str]] = []

    def line(
        d: dt.date,
        action: str,
        sym: str,
        desc: str,
        qty: str,
        px: str,
        amount: Decimal,
        as_of: dt.date | None = None,
    ) -> None:
        when = d.strftime("%m/%d/%Y") + (f" as of {as_of.strftime('%m/%d/%Y')}" if as_of else "")
        rows.append([when, action, sym, desc, qty, px, "", usd(amount)])

    for d in month_days(start, end, 3):
        amt = D(rng.choice([500, 1000, 1500]))
        line(d, "MoneyLink Transfer", "", "Tfr JPMORGAN CHASE BA, JANE DOE", "", "", amt)
        bank.add(
            d - dt.timedelta(days=rng.randint(0, 2)), "ONLINE TRANSFER TO SCHWAB BROKERAGE", -amt
        )
        t.transfers_in += amt
        t.paired += 1
        book.cash += amt
        sym = rng.choice(list(book.symbols))
        px = book.price(sym, d)
        qty = (book.cash * D("0.9") / px).to_integral_value(rounding="ROUND_FLOOR")
        if qty:
            cost = (qty * px).quantize(CENT)
            line(
                d + dt.timedelta(days=1), "Buy", sym, book.symbols[sym][0], f"{qty}", usd(px), -cost
            )
            book.cash -= cost
            book.shares[sym] += qty
    for d in month_days(start, end, 15):
        if d.month % 3 == 0 and book.shares["VTI"]:
            # A reinvested dividend is two lines: the dividend credit and the share purchase.
            div = (book.shares["VTI"] * D("0.93")).quantize(CENT)
            line(d, "Reinvest Dividend", "VTI", "VANGUARD TOTAL STOCK MARKET ETF", "", "", div)
            t.dividends += div
            book.cash += div
            px = book.price("VTI", d)
            qty = (div / px).quantize(D("0.0001"))
            cost = (qty * px).quantize(CENT)
            line(
                d,
                "Reinvest Shares",
                "VTI",
                "VANGUARD TOTAL STOCK MARKET ETF",
                f"{qty}",
                usd(px),
                -cost,
            )
            book.cash -= cost
            book.shares["VTI"] += qty
        if d.month % 6 == 0 and book.shares["NESN"]:
            div = (book.shares["NESN"] * D("1.21")).quantize(CENT)
            tax = (div * D("0.15")).quantize(CENT)
            line(d, "Cash Dividend", "NESN", "NESTLE SA ADR", "", "", div)
            line(d, "Foreign Tax Paid", "NESN", "NESTLE SA ADR", "", "", -tax)
            line(
                d + dt.timedelta(days=5),
                "ADR Mgmt Fee",
                "NESN",
                "NESTLE SA ADR",
                "",
                "",
                D("-0.62"),
            )
            t.dividends += div
            t.taxes += tax
            t.fees += D("0.62")
            book.cash += div - tax - D("0.62")
    for d in month_days(start, end, 28):
        if book.cash > 0:
            i = max(D("0.01"), (book.cash * D("0.0004")).quantize(CENT))
            line(d, "Bank Interest", "", "SCHWAB1 INT", "", "", i, as_of=d - dt.timedelta(days=1))
            t.interest += i
            book.cash += i
    rows.sort(
        key=lambda r: dt.datetime.strptime(r[0][:10], "%m/%d/%Y"), reverse=True
    )  # newest first
    body = [SCHWAB_HEADER, *rows]
    if legacy:
        body = [
            [
                f"Transactions  for account Individual ...234 as of {end.strftime('%m/%d/%Y')} 04:00 PM ET"
            ],
            *body,
        ]
        body.append(["Transactions Total", "", "", "", "", "", "", usd(D(0))])
    name = f"Individual_XXX234_Transactions_{end:%Y%m%d}-160000.csv"
    (out / name).write_text(_csv(body, quote_all=True), encoding="utf-8")

    snap = end
    pos = [
        ["Positions for account Individual ...234 as of 04:00 PM ET, " + snap.strftime("%Y/%m/%d")],
        [],
        SCHWAB_POS_HEADER,
    ]
    for sym, q in book.shares.items():
        if not q:
            continue
        px = book.price(sym, snap)
        mv = (q * px).quantize(CENT)
        pos.append(
            [
                sym,
                book.symbols[sym][0],
                f"{q}",
                usd(px),
                "$0.12",
                "0.05%",
                usd(mv),
                "$1.00",
                usd(mv * D("0.9")),
                usd(mv * D("0.1")),
                "40%",
                "ETFs & Closed End Funds",
            ]
        )
    pos.append(
        [
            "Cash & Cash Investments",
            "--",
            "--",
            "--",
            "--",
            "--",
            usd(book.cash),
            "$0.00",
            "--",
            "--",
            "5%",
            "Cash and Money Market",
        ]
    )
    total = book.value(snap)
    pos.append(
        [
            "Account Total",
            "--",
            "--",
            "--",
            "--",
            "--",
            usd(total),
            "$12.00",
            "--",
            "--",
            "--",
            "--",
        ]
    )
    (out / f"Individual-Positions-{snap:%Y-%m-%d}-160000.csv").write_text(
        _csv(pos, quote_all=True), encoding="utf-8"
    )
    t.marks[snap.isoformat()] = total
    return t


# ---------------------------------------------------------------- Fidelity
FID_HEADER = [
    "Run Date",
    "Account",
    "Account Number",
    "Action",
    "Symbol",
    "Description",
    "Type",
    "Price ($)",
    "Quantity",
    "Commission ($)",
    "Fees ($)",
    "Accrued Interest ($)",
    "Amount ($)",
    "Cash Balance ($)",
    "Settlement Date",
]
FID_POS_HEADER = [
    "Account Number",
    "Account Name",
    "Symbol",
    "Description",
    "Quantity",
    "Last Price",
    "Last Price Change",
    "Current Value",
    "Today's Gain/Loss Dollar",
    "Today's Gain/Loss Percent",
    "Total Gain/Loss Dollar",
    "Total Gain/Loss Percent",
    "Percent Of Account",
    "Cost Basis Total",
    "Average Cost Basis",
    "Type",
]
FID_DISCLAIMER = (
    "The data and information in this spreadsheet is provided to you solely for your use and is"
    " not for distribution. It is synthetic test data."
)


def fidelity(rng: random.Random, out: Path, bank: Bank, start: dt.date, end: dt.date) -> Truth:
    t = Truth("Assets:Fidelity:Retirement-6789", "fidelity")
    acct, number = "ROTH IRA", "Z23456789"
    book = Book(
        rng,
        {
            "FXAIX": ("FIDELITY 500 INDEX FUND", D("190")),
            "SPAXX": ("FIDELITY GOVERNMENT MONEY MARKET", D("1")),
        },
    )
    rows: list[list[str]] = []

    def line(
        d: dt.date, action: str, sym: str, desc: str, qty: str, px: str, amount: Decimal
    ) -> None:
        rows.append(
            [
                d.strftime("%m/%d/%Y"),
                acct,
                number,
                action,
                sym,
                desc,
                "Cash",
                px,
                qty,
                "",
                "",
                "",
                f"{amount:.2f}",
                f"{book.cash:.2f}",
                "",
            ]
        )

    for d in month_days(start, end, 10):
        amt = D("583.00")
        book.cash += amt
        line(d, "Electronic Funds Transfer Received (Cash)", "", "No Description", "0.000", "", amt)
        bank.add(d, "FID BKG SVC LLC MONEYLINE", -amt)
        t.transfers_in += amt
        t.paired += 1
        px = book.price("FXAIX", d)
        qty = (amt / px).quantize(D("0.001"))
        cost = (qty * px).quantize(CENT)
        book.cash -= cost
        book.shares["FXAIX"] += qty
        line(
            d + dt.timedelta(days=1),
            "YOU BOUGHT FIDELITY 500 INDEX FUND (FXAIX) (Cash)",
            "FXAIX",
            "FIDELITY 500 INDEX FUND",
            f"{qty}",
            f"{px}",
            -cost,
        )
        line(
            d + dt.timedelta(days=1),
            "REDEMPTION FROM CORE ACCOUNT FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash)",
            "SPAXX",
            "FIDELITY GOVERNMENT MONEY MARKET",
            f"{-cost}",
            "1",
            D(0),
        )
    for d in month_days(start, end, 30):
        if book.cash > 0:
            div = max(CENT, (book.cash * D("0.004")).quantize(CENT))
            book.cash += div
            line(
                d,
                "DIVIDEND RECEIVED FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash)",
                "SPAXX",
                "FIDELITY GOVERNMENT MONEY MARKET",
                "0.000",
                "",
                div,
            )
            line(
                d,
                "REINVESTMENT FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash)",
                "SPAXX",
                "FIDELITY GOVERNMENT MONEY MARKET",
                f"{div}",
                "1",
                D(0),
            )
            t.dividends += div
        if d.month in (3, 6, 9, 12) and book.shares["FXAIX"]:
            div = (book.shares["FXAIX"] * D("0.71")).quantize(CENT)
            book.cash += div
            line(
                d,
                "DIVIDEND RECEIVED FIDELITY 500 INDEX FUND (FXAIX) (Cash)",
                "FXAIX",
                "FIDELITY 500 INDEX FUND",
                "0.000",
                "",
                div,
            )
            t.dividends += div
            px = book.price("FXAIX", d)
            qty = (div / px).quantize(D("0.001"))
            cost = (qty * px).quantize(CENT)
            book.cash -= cost
            book.shares["FXAIX"] += qty
            line(
                d,
                "REINVESTMENT FIDELITY 500 INDEX FUND (FXAIX) (Cash)",
                "FXAIX",
                "FIDELITY 500 INDEX FUND",
                f"{qty}",
                f"{px}",
                -cost,
            )
    rows.sort(key=lambda r: dt.datetime.strptime(r[0], "%m/%d/%Y"), reverse=True)
    body: list[list[str]] = [
        [],
        [],
        FID_HEADER,
        *rows,
        [],
        [FID_DISCLAIMER],
        [f"Date downloaded {end:%m/%d/%Y} 4:05 PM ET"],
    ]
    (out / "Accounts_History.csv").write_text(_csv(body), encoding="utf-8")

    pos = [FID_POS_HEADER]
    px = book.price("FXAIX", end)
    mv = (book.shares["FXAIX"] * px).quantize(CENT)
    pos.append(
        [
            number,
            acct,
            "SPAXX**",
            "HELD IN MONEY MARKET",
            "",
            "",
            "",
            f"${book.cash - D('12.34'):,.2f}",
            "",
            "",
            "",
            "",
            "3%",
            "",
            "",
            "Cash",
        ]
    )
    pos.append(
        [
            number,
            acct,
            "FXAIX",
            "FIDELITY 500 INDEX FUND",
            f"{book.shares['FXAIX']}",
            f"${px}",
            "+$0.50",
            f"${mv:,.2f}",
            "+$10.00",
            "+0.2%",
            "+$100.00",
            "+3%",
            "97%",
            f"${mv * D('0.95'):,.2f}",
            f"${px}",
            "Cash",
        ]
    )
    pos.append(
        [number, acct, "Pending Activity", "", "", "", "", "$12.34", "", "", "", "", "", "", "", ""]
    )
    pos += [[], [FID_DISCLAIMER], [f"Date downloaded {end:%b-%d-%Y} 4:05 p.m ET"]]
    (out / f"Portfolio_Positions_{end:%b-%d-%Y}.csv").write_text(_csv(pos), encoding="utf-8")
    t.marks[end.isoformat()] = book.cash + mv
    return t


# ---------------------------------------------------------------- IBKR
def ibkr(rng: random.Random, out: Path, bank: Bank, start: dt.date, end: dt.date) -> Truth:
    t = Truth("Assets:IBKR:Brokerage-4321", "ibkr")
    prior = D(rng.randint(20000, 60000)) + D("0.37")
    rows: list[list[str]] = [
        ["Statement", "Header", "Field Name", "Field Value"],
        ["Statement", "Data", "BrokerName", "Interactive Brokers LLC"],
        ["Statement", "Data", "Title", "Activity Statement"],
        [
            "Statement",
            "Data",
            "Period",
            f"{start:%B} {start.day}, {start.year} - {end:%B} {end.day}, {end.year}",
        ],
        ["Account Information", "Header", "Field Name", "Field Value"],
        ["Account Information", "Data", "Name", "Jane Doe"],
        ["Account Information", "Data", "Account", "U7654321"],
        ["Account Information", "Data", "Account Type", "Individual"],
        ["Account Information", "Data", "Base Currency", "USD"],
    ]
    flows: dict[str, list[list[str]]] = defaultdict(list)
    for d in month_days(start, end, 5):
        if rng.random() < 0.6:
            amt = D(rng.choice([2000, 2500]))
            flows["Deposits & Withdrawals"].append(
                ["USD", d.isoformat(), "Electronic Fund Transfer", f"{amt}"]
            )
            bank.add(d - dt.timedelta(days=1), "ACH TRANSFER INTERACTIVE BROKERS", -amt)
            t.transfers_in += amt
            t.paired += 1
    if t.paired == 0:  # at least one deposit so the pairing check means something
        flows["Deposits & Withdrawals"].append(
            ["USD", start.replace(day=5).isoformat(), "Electronic Fund Transfer", "1000"]
        )
        bank.add(start.replace(day=4), "ACH TRANSFER INTERACTIVE BROKERS", D(-1000))
        t.transfers_in += 1000
        t.paired += 1
    wd = D("750")
    flows["Deposits & Withdrawals"].append(
        ["USD", end.replace(day=20).isoformat(), "Disbursement Initiated by Jane Doe", f"-{wd}"]
    )
    bank.add(end.replace(day=21), "INTERACTIVE BROKERS DEPOSIT", wd)
    t.transfers_in -= wd
    t.paired += 1
    total = sum(D(r[3]) for r in flows["Deposits & Withdrawals"])
    flows["Deposits & Withdrawals"].append(["Total", "", "", f"{total}"])
    for d in month_days(start, end, 12):
        if d.month % 3 == 0:
            div = D(rng.randint(40, 90)) + D("0.25")
            flows["Dividends"].append(
                [
                    "USD",
                    d.isoformat(),
                    "QQQ(US46090E1038) Cash Dividend USD 0.71 per Share (Ordinary Dividend)",
                    f"{div}",
                ]
            )
            tax = (div * D("0.1")).quantize(CENT)
            flows["Withholding Tax"].append(
                ["USD", d.isoformat(), "QQQ(US46090E1038) Cash Dividend - US Tax", f"-{tax}", ""]
            )
            t.dividends += div
            t.taxes += tax
    eur = D("20.00")
    flows["Dividends"].append(
        [
            "EUR",
            end.replace(day=2).isoformat(),
            "ASML(NL0010273215) Cash Dividend EUR 1.60 per Share",
            f"{eur}",
        ]
    )
    t.dividends += (eur * D("1.0850")).quantize(CENT)
    flows["Dividends"].append(["Total", "", "", "999.99"])  # per-currency totals are not lines
    flows["Dividends"].append(["Total in USD", "", "", "999.99"])
    for d in month_days(start, end, 4):
        i = D(rng.randint(3, 30)) + D("0.11")
        flows["Interest"].append(
            ["USD", d.isoformat(), f"USD Credit Interest for {d:%b-%Y}", f"{i}"]
        )
        t.interest += i
    flows["Fees"].append(
        ["Other Fees", "USD", end.replace(day=3).isoformat(), "Market data fee", "-10.00"]
    )
    t.fees += D("10.00")
    headers = {
        "Deposits & Withdrawals": ["Currency", "Settle Date", "Description", "Amount"],
        "Dividends": ["Currency", "Date", "Description", "Amount"],
        "Withholding Tax": ["Currency", "Date", "Description", "Amount", "Code"],
        "Interest": ["Currency", "Date", "Description", "Amount"],
        "Fees": ["Subtitle", "Currency", "Date", "Description", "Amount"],
    }
    gain = D(rng.randint(-3000, 6000)) + D("0.18")
    flow_sum = t.transfers_in + t.dividends + t.interest - t.fees - t.taxes
    current = prior + flow_sum + gain
    rows += [
        [
            "Net Asset Value",
            "Header",
            "Asset Class",
            "Prior Total",
            "Current Long",
            "Current Short",
            "Current Total",
            "Change",
        ],
        ["Net Asset Value", "Data", "Cash ", "100.00", "200.00", "0", "200.00", "100.00"],
        [
            "Net Asset Value",
            "Data",
            "Stock",
            f"{prior - 100}",
            f"{current - 200}",
            "0",
            f"{current - 200}",
            "0",
        ],
        [
            "Net Asset Value",
            "Data",
            "Total",
            f"{prior}",
            f"{current}",
            "0",
            f"{current}",
            f"{current - prior}",
        ],
        ["Net Asset Value", "Header", "Time Weighted Rate of Return"],
        ["Net Asset Value", "Data", "4.2%"],
        [
            "Trades",
            "Header",
            "DataDiscriminator",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date/Time",
            "Quantity",
            "T. Price",
            "Proceeds",
            "Comm/Fee",
        ],
        [
            "Trades",
            "Data",
            "Order",
            "Stocks",
            "USD",
            "QQQ",
            f"{start.replace(day=8).isoformat()}, 10:01:02",
            "5",
            "480.10",
            "-2400.50",
            "-1.00",
        ],
    ]
    for section, lines in flows.items():
        rows.append([section, "Header", *headers[section]])
        rows += [[section, "Data", *line] for line in lines]
    rows += [
        ["Base Currency Exchange Rate", "Header", "Currency", "Rate"],
        ["Base Currency Exchange Rate", "Data", "EUR", "1.0850"],
    ]
    (out / f"U7654321_{start:%Y%m%d}_{end:%Y%m%d}.csv").write_text(_csv(rows), encoding="utf-8")
    t.marks[(start - dt.timedelta(days=1)).isoformat()] = prior
    t.marks[end.isoformat()] = current
    return t


# ---------------------------------------------------------------- 宁波银行
NB_HEADER = [
    "交易日期",
    "交易时间",
    "摘要",
    "收入金额",
    "支出金额",
    "账户余额",
    "对方户名",
    "对方账号",
    "交易渠道",
    "附言",
]
NB_CSV_HEADER = ["记账日期", "交易摘要", "交易金额", "借贷标志", "余额", "对方户名", "备注"]


def ningbo(rng: random.Random, out: Path, start: dt.date, end: dt.date) -> list[Truth]:
    from openpyxl import Workbook

    truths = []
    for hint, fmt in (("1234", "xlsx"), ("5678", "csv")):
        t = Truth(f"Assets:Bank of Ningbo:Checking-{hint}", "ningbo")
        bal = D(rng.randint(5000, 30000)) + D("0.50")
        lines: list[tuple[dt.date, str, Decimal, str, str]] = []
        for d in month_days(start, end, 8):
            lines.append((d, "代发工资", D("15800.00"), "示例科技有限公司", "工资"))
            lines.append(
                (
                    d + dt.timedelta(days=2),
                    "快捷支付",
                    -D(rng.randint(100, 900)) - D("0.30"),
                    "财付通支付科技有限公司",
                    "微信支付",
                )
            )
            lines.append((d + dt.timedelta(days=5), "转账", -D("3000.00"), "李四", "房租"))
            lines.append(
                (d + dt.timedelta(days=12), "结息", D(rng.randint(1, 9)) + D("0.21"), "", "利息")
            )
        lines.sort(key=lambda x: x[0])
        rows = []
        for d, summary, amt, payee, note in lines:
            bal += amt
            rows.append((d, summary, amt, payee, note, bal))
        t.closing = (rows[-1][0].isoformat(), rows[-1][5])
        t.interest = sum((amt for _, summary, amt, *_ in rows if summary == "结息"), ZERO)
        preamble = [
            ["宁波银行个人账户交易明细"],
            ["户名：张三", "", f"账号：6228 **** **** {hint}", "", "币种：人民币"],
            [f"起止日期：{start:%Y-%m-%d} 至 {end:%Y-%m-%d}"],
        ]
        if fmt == "xlsx":
            wb = Workbook()
            ws = wb.active
            assert ws is not None
            for r in preamble:
                ws.append(r)
            ws.append(NB_HEADER)
            for d, summary, amt, payee, note, b in rows:
                ws.append(
                    [
                        d.strftime("%Y-%m-%d"),
                        "10:20:30",
                        summary,
                        float(amt) if amt > 0 else None,
                        float(-amt) if amt < 0 else None,
                        float(b),
                        payee,
                        "/",
                        "手机银行",
                        note,
                    ]
                )
            ws.append(["合计", "", "", "", "", "", "", "", "", ""])
            wb.save(out / f"宁波银行交易明细_{hint}_{end:%Y%m}.xlsx")
        else:
            body = [*preamble, NB_CSV_HEADER]
            for d, summary, amt, payee, note, b in reversed(rows):  # newest first, like many banks
                body.append(
                    [
                        d.strftime("%Y%m%d"),
                        summary,
                        f"{abs(amt):.2f}",
                        "贷" if amt > 0 else "借",
                        f"{b:.2f}",
                        payee,
                        note,
                    ]
                )
            (out / f"nbcb_detail_{hint}_{end:%Y%m}.csv").write_bytes(_csv(body).encode("gb18030"))
        truths.append(t)
    return truths


# ---------------------------------------------------------------- OKX
def okx(rng: random.Random, out: Path, start: dt.date, end: dt.date) -> Truth:
    t = Truth("Assets:OKX:Crypto", "okx")
    rows = [
        ["Bill ID", "Time", "Type", "Amount", "Before Balance", "After Balance", "Fee", "Currency"]
    ]
    bal: dict[str, Decimal] = defaultdict(D)
    n = 0

    def bill(d: dt.date, kind: str, amount: Decimal, ccy: str, fee: Decimal = ZERO) -> None:
        nonlocal n
        n += 1
        before = bal[ccy]
        bal[ccy] += amount - fee
        rows.append(
            [
                f"OKXB{n:05d}",
                f"{d.isoformat()} 08:{n % 60:02d}:00",
                kind,
                f"{amount}",
                f"{before}",
                f"{bal[ccy]}",
                f"{fee}",
                ccy,
            ]
        )

    for d in month_days(start, end, 6):
        amt = D(rng.choice([200, 300, 500]))
        bill(d, "Deposit", amt, "USDT")
        t.transfers_in += amt
        t.unpaired += 1  # comes from another wallet, no statement for the other side
        bill(d + dt.timedelta(days=1), "Trade", -amt / 2, "USDT")
        bill(d + dt.timedelta(days=1), "Trade", D("0.0011"), "BTC")
        bill(d + dt.timedelta(days=2), "Transfer to trading account", -D("10"), "USDT")
        if d.month % 2 == 0:
            i = D(rng.randint(1, 4)) + D("0.37")
            bill(d + dt.timedelta(days=20), "Simple Earn interest", i, "USDC")
            t.interest += i
    bill(end - dt.timedelta(days=3), "Deposit", D("0.05"), "BTC")  # no price: skipped
    bill(end - dt.timedelta(days=2), "Withdrawal", -D("150"), "USDT", fee=D("1"))
    t.transfers_in -= D("150")
    t.fees += D("1")
    t.unpaired += 1
    (out / f"OKX_funding_bills_{end:%Y%m%d}.csv").write_text(_csv(rows), encoding="utf-8")
    value = D(rng.randint(3000, 9000)) + D("0.42")
    snap = [
        ["OKX asset snapshot"],
        ["Snapshot time", f"{end.isoformat()} 23:59:59"],
        ["Currency", "Total", "Value (USD)"],
        ["BTC", "0.0150", f"{value - D('500.00')}"],
        ["USDT", "500.00", "500.00"],
    ]
    (out / f"OKX_assets_{end:%Y%m%d}.csv").write_text(_csv(snap), encoding="utf-8")
    t.marks[end.isoformat()] = value
    return t


# ---------------------------------------------------------------- household
def household(
    out: Path,
    seed: int = 42,
    start: dt.date = dt.date(2026, 1, 1),
    end: dt.date = dt.date(2026, 9, 30),
) -> dict[str, Truth]:
    """Write one household's exports into `out`; return ground truth per account."""
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    bank = Bank()
    for d in month_days(start, end, 1):
        bank.add(d, "ACME CORP PAYROLL PPD ID: 9000000001", D("6400.00"))
    truths = [
        schwab(rng, out, bank, start, end, legacy=seed % 2 == 1),
        fidelity(rng, out, bank, start, end),
        ibkr(rng, out, bank, start, end),
        *ningbo(rng, out, start, end),
        okx(rng, out, start, end),
    ]
    bank.write(out / f"Chase0001_Activity_{end:%Y%m%d}.CSV", opening=D("8000.00"))
    return {t.account: t for t in truths}
