"""Generate SYNTHETIC statement exports for a fictional household (deterministic, seed=42).

Writes fixtures/demo/statements/*.csv in the same layouts as the generic CSV profiles. Nothing
here is real data. Re-run with:  uv run --project backend python fixtures/demo/generate.py
"""

from __future__ import annotations

import csv
import datetime as dt
import random
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).parent / "statements"
START = dt.date(2024, 11, 1)
END = dt.date(2026, 10, 15)
rng = random.Random(42)

CHECKING, FREEDOM, AMEX = "checking", "freedom", "amex"
D = Decimal


def money(lo: float, hi: float) -> Decimal:
    return D(str(round(rng.uniform(lo, hi), 2)))


def months(start: dt.date, end: dt.date):
    d = start.replace(day=1)
    while d <= end:
        yield d
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


# txns[account] -> list of (date, description, amount_in_ledger_sign, category)
txns: dict[str, list[tuple[dt.date, str, Decimal, str]]] = defaultdict(list)


def add(acct: str, day: dt.date, desc: str, amount: Decimal, cat: str = "") -> None:
    if START <= day <= END:
        txns[acct].append((day, desc, amount, cat))


for m in months(START, END):
    y, mo = m.year, m.month
    raise_ = m >= dt.date(2025, 7, 1)
    pay = D("4210.55") if raise_ else D("3875.20")
    for day in (1, 15):
        add(CHECKING, dt.date(y, mo, day), "ACME CORP PAYROLL PPD ID: 9000000001", pay)
    add(CHECKING, dt.date(y, mo, 1), "ZELLE PAYMENT TO OAKWOOD APARTMENTS", D("-2350.00"))
    add(CHECKING, dt.date(y, mo, 7), "PSE&G ELECTRIC AUTOPAY", -money(60, 140))
    add(CHECKING, dt.date(y, mo, 12), "VERIZON WIRELESS PAYMENTS", D("-85.00"))
    add(CHECKING, dt.date(y, mo, 20), "VANGUARD BUY INVESTMENT", D("-1500.00"))
    add(CHECKING, dt.date(y, mo, 28), "INTEREST PAYMENT", money(0.5, 3))
    if rng.random() < 0.4:
        add(CHECKING, dt.date(y, mo, rng.randint(2, 27)), "ATM WITHDRAWAL 000123 MAIN ST", D("-100.00"))

    # Chase Freedom: everyday spend
    for _ in range(rng.randint(5, 8)):
        add(FREEDOM, dt.date(y, mo, rng.randint(1, 28)), "WHOLEFDS PRN 10234", -money(25, 160), "Groceries")
    for _ in range(rng.randint(2, 4)):
        add(FREEDOM, dt.date(y, mo, rng.randint(1, 28)), "TRADER JOE S #555", -money(20, 90), "Groceries")
    for _ in range(rng.randint(1, 3)):
        add(FREEDOM, dt.date(y, mo, rng.randint(1, 28)), "SHELL OIL 57444", -money(35, 65), "Gas")
    netflix = D("-17.99") if m >= dt.date(2025, 9, 1) else D("-15.49")
    add(FREEDOM, dt.date(y, mo, 9), "NETFLIX.COM", netflix, "Bills & Utilities")
    add(FREEDOM, dt.date(y, mo, 3), "SPOTIFY USA", D("-11.99"), "Bills & Utilities")
    if m >= dt.date(2026, 4, 1):  # a new subscription appears
        add(FREEDOM, dt.date(y, mo, 18), "CLAUDE.AI SUBSCRIPTION", D("-20.00"), "Shopping")

    # Amex Gold: dining, travel, Amazon (Amex convention handled by the profile)
    for _ in range(rng.randint(4, 9)):
        add(AMEX, dt.date(y, mo, rng.randint(1, 28)), rng.choice([
            "SWEETGREEN NEW YORK", "JOE'S PIZZA", "BLUE BOTTLE COFFEE", "UBER EATS", "NOBU NYC",
        ]), -money(12, 120), "Restaurant")
    for _ in range(rng.randint(1, 4)):
        add(AMEX, dt.date(y, mo, rng.randint(1, 28)), "AMAZON.COM*AB12CD", -money(10, 200), "Merchandise")
    if mo in (3, 8, 12):
        add(AMEX, dt.date(y, mo, 5), "DELTA AIR LINES", -money(300, 700), "Travel")
    if (y, mo) == (2026, 2):
        add(AMEX, dt.date(y, mo, 14), "TIFFANY & CO", D("-1850.00"), "Merchandise")

# Card payments: each month pay the previous month's card spend from checking. They appear on
# BOTH sides (checking debit + card credit) -> internal transfers for M3 to pair.
for card, out_desc, in_desc, delay in (
    (FREEDOM, "CHASE CREDIT CRD AUTOPAY", "AUTOMATIC PAYMENT - THANK", 25),
    (AMEX, "AMEX EPAYMENT ACH PMT", "AUTOPAY PAYMENT RECEIVED - THANK YOU", 22),
):
    by_month: dict[tuple[int, int], Decimal] = defaultdict(D)
    for d, _, amt, _ in txns[card]:
        by_month[(d.year, d.month)] += amt
    for (y, mo), spent in sorted(by_month.items()):
        nxt = (dt.date(y, mo, 1) + dt.timedelta(days=32)).replace(day=delay)
        add(CHECKING, nxt, out_desc, spent)  # spent is negative -> checking goes down
        add(card, nxt + dt.timedelta(days=1), in_desc, -spent)

OPENING_CHECKING = D("8420.17")


def write_checking(rows, path: Path) -> None:
    rows = sorted(rows, key=lambda r: r[0])
    bal = OPENING_CHECKING + sum((r[2] for r in txns[CHECKING] if r[0] < rows[0][0]), D(0))
    out = []
    for d, desc, amt, _ in rows:
        bal += amt
        out.append([
            "CREDIT" if amt > 0 else "DEBIT", d.strftime("%m/%d/%Y"), desc, f"{amt:.2f}",
            "ACH_CREDIT" if amt > 0 else "ACH_DEBIT", f"{bal:.2f}", "",
        ])
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"])
        w.writerows(reversed(out))  # Chase exports newest first


def write_freedom(rows, path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"])
        for d, desc, amt, cat in sorted(rows, key=lambda r: r[0], reverse=True):
            w.writerow([d.strftime("%m/%d/%Y"), (d + dt.timedelta(days=1)).strftime("%m/%d/%Y"),
                        desc, cat, "Payment" if amt > 0 else "Sale", f"{amt:.2f}", ""])


def write_amex(rows, path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Description", "Amount", "Category"])
        for d, desc, amt, cat in sorted(rows, key=lambda r: r[0], reverse=True):
            w.writerow([d.strftime("%m/%d/%Y"), desc, f"{-amt:.2f}", cat])  # charges positive


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.csv"):
        old.unlink()
    for m in months(START, END):
        key = m.strftime("%Y%m")
        in_month = lambda acct: [r for r in txns[acct] if (r[0].year, r[0].month) == (m.year, m.month)]  # noqa: E731
        if rows := in_month(CHECKING):
            write_checking(rows, OUT / f"Chase0001_Activity_{key}.CSV")
        if rows := in_month(FREEDOM):
            write_freedom(rows, OUT / f"Chase0002_Activity_{key}.CSV")
        if rows := in_month(AMEX):
            write_amex(rows, OUT / f"amex_activity_{key}.csv")
    # An overlapping export (Aug 15 - Sep 30 2026) to exercise dedup.
    overlap = [r for r in txns[CHECKING] if dt.date(2026, 8, 15) <= r[0] <= dt.date(2026, 9, 30)]
    write_checking(overlap, OUT / "Chase0001_Activity_20260815_20260930.CSV")
    write_decisions(OUT.parent / "decisions.yaml")
    print(f"wrote {len(list(OUT.glob('*')))} files to {OUT}")


def write_decisions(path: Path) -> None:
    """The fictional user's own judgement: two rules + month-end Vanguard market values."""
    mk = random.Random(7)
    value = D(0)
    vals = []
    for m in months(START, END):
        contrib = sum((-a for d, desc, a, _ in txns[CHECKING]
                       if desc.startswith("VANGUARD") and (d.year, d.month) == (m.year, m.month)), D(0))
        r = D(str(round(mk.gauss(0.007, 0.04), 4)))
        value = (value * (1 + r) + contrib).quantize(D("0.01"))
        month_end = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        if month_end <= END:
            vals.append({"account": "Assets:Vanguard:Brokerage", "date": month_end.isoformat(),
                         "value": str(value)})
    lines = [
        "# Synthetic user decisions for the demo ledger (applied by `make seed-demo`).",
        "version: 1",
        "rules:",
        "  - {pattern: oakwood apartments, target_account: 'Expenses:Housing:Rent', payee: Oakwood}",
        "  - {pattern: vanguard buy, target_account: 'Assets:Vanguard:Brokerage', payee: Vanguard}",
        "valuations:",
        *[f"  - {{account: '{v['account']}', date: '{v['date']}', value: '{v['value']}'}}" for v in vals],
    ]
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
