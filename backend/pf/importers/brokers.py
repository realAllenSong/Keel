"""Brokerage exports: Charles Schwab, Fidelity, Interactive Brokers.

A brokerage account is booked as ONE account valued in its base currency, not share by share:
the ledger has no daily security prices, and what the reports need is the account's value and
the money that crossed its edge. So each export contributes two things:

  cash flows   deposits / withdrawals (left uncategorised so they pair with the bank side as
               transfers), dividends, interest, fees and withheld tax (booked straight to their
               category: the statement says what they are)
  value marks  the positions page's account total (Schwab, Fidelity) or the statement's net asset
               value (IBKR), stored as valuations; gains are what the marks add on top of flows

Buys, sells, reinvestments and core money-market sweeps move value inside the account and are
skipped. A line the classifier does not know is kept uncategorised for review, unless it carries
a quantity of a security (then it is a trade or a share movement, which moves no cash in or out).

Layouts follow the exports as documented by each broker and are unverified against real files
until a sample is checked; the synthetic fixtures in fixtures/brokers mirror them.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from decimal import Decimal
from pathlib import Path

from pf.importers.base import ImportError_, ParsedStatement, RawTxn
from pf.money import MoneyError, parse_decimal

DIVIDENDS = "Income:Dividends"
INTEREST = "Income:Interest"
FEES = "Expenses:Fees:Investment"
TAXES = "Expenses:Taxes"
TRANSFER = "transfer"  # uncategorised on purpose: the bank side pairs with it
SKIP = "skip"

RETIREMENT = re.compile(r"\b(ira|roth|401\s?\(?k\)?|403\s?\(?b\)?|sep|hsa|rollover)\b", re.I)


def read_csv(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes almost anything
        raise ImportError_(f"{path.name}: cannot decode file")
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(content))]


def money(s: str) -> Decimal | None:
    """'$1,234.56', '-$1.00', '($1.00)', '--' (none)."""
    s = s.strip()
    if s in ("", "--", "-", "n/a", "N/A"):
        return None
    try:
        return parse_decimal(s)
    except MoneyError:
        return None


def us_date(s: str) -> dt.date | None:
    """'10/15/2026', '10/15/2026 as of 10/14/2026' (the first date is when cash moved)."""
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if not m:
        return None
    return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))


def classify(action: str, quantity: Decimal | None, symbol: str) -> str:
    """Map a broker's action text to a category, TRANSFER or SKIP. Order matters: 'reinvest
    dividend' is the dividend itself, 'reinvest shares' / 'reinvestment' is the buy."""
    a = action.lower()
    if re.search(r"(?<!non-)\btax(?![- ]exempt)|nra adj|withholding", a):
        return TAXES
    if re.search(r"div|cap gain|capital gain|distribution of capital|in lieu", a) and not re.search(
        r"reinvest(ment| shares)", a
    ):
        return DIVIDENDS
    if "margin interest" in a or re.search(r"\bfee|commission|adr mgmt|advisory", a):
        return FEES
    if "interest" in a:
        return INTEREST
    if re.search(
        r"buy|bought|sell|sold|reinvest|exchange|split|merger|spin|redemption|purchase|"
        r"core account|expired|assigned|exercise|security transfer|journaled shares|conversion",
        a,
    ):
        return SKIP
    if re.search(
        r"transfer|moneylink|wire|deposit|withdraw|funds|journal|check|contribution|"
        r"distribution|ach|eft|electronic",
        a,
    ):
        return TRANSFER
    return SKIP if quantity and symbol else "unknown"


def flow(
    date: dt.date, amount: Decimal, kind: str, description: str, ref: str | None = None
) -> RawTxn | None:
    """One cash-flow line, or None for moves inside the account."""
    if kind == SKIP or amount == 0:
        return None
    return RawTxn(
        date=date,
        amount=amount,
        description=description,
        ref=ref,
        category=None if kind in (TRANSFER, "unknown") else kind,
    )


def _hint(*texts: str, digits: int = 4) -> str | None:
    """Last digits of a masked account number: '...234', 'XXXX1234', 'Z12345678', 'U1234567'."""
    for t in texts:
        m = re.search(r"(?:\.\.\.|X{2,}-?|\*{2,}|[A-Z]\d{3,}?)(\d{3,})", t or "")
        if m:
            return m.group(1)[-digits:]
    return None


def _statement(
    name: str,
    institution: str,
    rows: list[RawTxn],
    *,
    hint: str | None,
    account_kind: str = "",
    currency: str = "USD",
    valuations: list[tuple[dt.date, Decimal]] | None = None,
) -> ParsedStatement:
    return ParsedStatement(
        importer=name,
        institution=institution,
        currency=currency,
        rows=rows,
        account_type="Assets",
        asset_class="retirement" if RETIREMENT.search(account_kind) else "brokerage",
        account_hint=hint,
        valuations=valuations or [],
    )


def _header_index(rows: list[list[str]], *need: str, scan: int = 30) -> int | None:
    for i, row in enumerate(rows[:scan]):
        low = [c.lower() for c in row]
        if all(any(c == n or c.startswith(n) for c in low) for n in need):
            return i
    return None


def _col(header: list[str], *names: str) -> int | None:
    low = [h.lower() for h in header]
    for n in names:
        for i, h in enumerate(low):
            if h == n or h.startswith(n):
                return i
    return None


def _cell(row: list[str], i: int | None) -> str:
    return row[i] if i is not None and i < len(row) else ""


# ---------------------------------------------------------------- Charles Schwab
class SchwabImporter:
    """Schwab History export (Date, Action, Symbol, Description, Quantity, Price, Fees & Comm,
    Amount) and Positions export (title line '... ...234 as of <time>, YYYY/MM/DD', a table with
    'Mkt Val (Market Value)' and an 'Account Total' row). Hints are the last 3 digits, the most
    Schwab shows ('...234')."""

    name = "schwab"
    institution = "Schwab"

    def sniff(self, path: Path, head: str) -> bool:
        if path.suffix.lower() != ".csv":
            return False
        low = head.lower()
        history = all(
            f'"{c}"' in low or f",{c}," in low or low.startswith(c)
            for c in ("date", "action", "symbol", "fees & comm", "amount")
        )
        positions = "positions for" in low and "mkt val" in low
        return history or positions

    def parse(self, path: Path) -> ParsedStatement:
        rows = read_csv(path)
        title = " ".join(rows[0]) if rows else ""
        if "positions for" in title.lower():
            return self._positions(path, rows, title)
        h = _header_index(rows, "date", "action", "amount")
        if h is None:
            raise ImportError_(f"{path.name}: Schwab history header not found")
        header = rows[h]
        c = {
            k: _col(header, k)
            for k in ("date", "action", "symbol", "description", "quantity", "amount")
        }
        out: list[RawTxn] = []
        for row in rows[h + 1 :]:
            date = us_date(_cell(row, c["date"]))
            amount = money(_cell(row, c["amount"]))
            if date is None or amount is None:
                continue  # 'Transactions Total' footer, blank lines
            action = _cell(row, c["action"])
            symbol = _cell(row, c["symbol"])
            kind = classify(action, money(_cell(row, c["quantity"])), symbol)
            desc = " · ".join(x for x in (action, symbol, _cell(row, c["description"])) if x)
            if t := flow(date, amount, kind, desc):
                out.append(t)
        hint = _hint(path.name, title, digits=3)
        return _statement(self.name, self.institution, out, hint=hint, account_kind=title)

    def _positions(self, path: Path, rows: list[list[str]], title: str) -> ParsedStatement:
        m = re.search(r"(\d{4})/(\d{2})/(\d{2})", title) or re.search(
            r"(\d{4})-(\d{2})-(\d{2})", path.name
        )
        if not m:
            raise ImportError_(f"{path.name}: positions date not found in the title line")
        as_of = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        h = _header_index(rows, "symbol", "mkt val")
        if h is None:
            raise ImportError_(f"{path.name}: Schwab positions header not found")
        value_col = _col(rows[h], "mkt val", "market value")
        totals = [
            money(_cell(r, value_col)) for r in rows[h + 1 :] if r and r[0] == "Account Total"
        ]
        if len(totals) != 1 or totals[0] is None:
            raise ImportError_(
                f"{path.name}: expected exactly one 'Account Total' row; export positions for a"
                " single account"
            )
        return _statement(
            self.name,
            self.institution,
            [],
            hint=_hint(title, path.name, digits=3),
            account_kind=title,
            valuations=[(as_of, totals[0])],
        )


# ---------------------------------------------------------------- Fidelity
class FidelityImporter:
    """Fidelity Activity & Orders history (Run Date, Account, [Account Number], Action, Symbol,
    ..., Quantity, ..., Amount ($), Settlement Date) and Portfolio Positions (Account Number,
    Account Name, Symbol, ..., Current Value). Both carry the account number per row; a file
    spanning several accounts is refused (download one account at a time)."""

    name = "fidelity"
    institution = "Fidelity"

    def sniff(self, path: Path, head: str) -> bool:
        if path.suffix.lower() != ".csv":
            return False
        low = head.lower()
        history = "run date" in low and "action" in low and "amount" in low
        positions = "account number" in low and "account name" in low and "current value" in low
        return history or positions

    def parse(self, path: Path) -> ParsedStatement:
        rows = read_csv(path)
        if (h := _header_index(rows, "run date", "action", "amount")) is not None:
            return self._history(path, rows, h)
        if (
            h := _header_index(rows, "account number", "account name", "current value")
        ) is not None:
            return self._positions(path, rows, h)
        raise ImportError_(f"{path.name}: Fidelity header not found")

    @staticmethod
    def _one_account(path: Path, numbers: set[str]) -> str | None:
        numbers.discard("")
        if len(numbers) > 1:
            raise ImportError_(
                f"{path.name}: this file holds {len(numbers)} Fidelity accounts; download each"
                " account separately (pick the account first, then download)"
            )
        return next(iter(numbers), None)

    def _history(self, path: Path, rows: list[list[str]], h: int) -> ParsedStatement:
        header = rows[h]
        c = {
            "date": _col(header, "run date"),
            "account": _col(header, "account"),
            "number": _col(header, "account number"),
            "action": _col(header, "action"),
            "symbol": _col(header, "symbol"),
            "description": _col(header, "description", "security description"),
            "quantity": _col(header, "quantity"),
            "amount": _col(header, "amount"),
        }
        if c["account"] == c["number"]:  # "account" prefix matched "account number" first
            c["account"] = next(
                (i for i, x in enumerate(header) if x.lower() == "account"), c["account"]
            )
        out: list[RawTxn] = []
        numbers: set[str] = set()
        kinds: set[str] = set()
        for row in rows[h + 1 :]:
            date = us_date(_cell(row, c["date"]))
            amount = money(_cell(row, c["amount"]))
            if date is None or amount is None:
                continue  # disclaimer footer, blank lines
            numbers.add(_cell(row, c["number"]) or _cell(row, c["account"]))
            kinds.add(_cell(row, c["account"]))
            action = _cell(row, c["action"])
            kind = classify(action, money(_cell(row, c["quantity"])), _cell(row, c["symbol"]))
            if t := flow(date, amount, kind, re.sub(r"\s+", " ", action)):
                out.append(t)
        number = self._one_account(path, numbers)
        return _statement(
            self.name,
            self.institution,
            out,
            hint=_hint(number or "", path.name),
            account_kind=" ".join(kinds),
        )

    def _positions(self, path: Path, rows: list[list[str]], h: int) -> ParsedStatement:
        header = rows[h]
        num, name, value = (
            _col(header, "account number"),
            _col(header, "account name"),
            _col(header, "current value"),
        )
        total = Decimal(0)
        numbers: set[str] = set()
        names: set[str] = set()
        as_of: dt.date | None = None
        for row in rows[h + 1 :]:
            joined = " ".join(row)
            if m := re.search(r"Date downloaded\s+(\w{3})[- ](\d{1,2})[-, ]+(\d{4})", joined):
                as_of = dt.datetime.strptime(" ".join(m.groups()), "%b %d %Y").date()
            elif m := re.search(r"Date downloaded\s+(\d{1,2}/\d{1,2}/\d{4})", joined):
                as_of = us_date(m.group(1))
            v = money(_cell(row, value))
            if v is None or not _cell(row, num):
                continue
            numbers.add(_cell(row, num))
            names.add(_cell(row, name))
            total += v  # money market (SPAXX**) and 'Pending Activity' rows are part of it
        if as_of is None and (
            m := re.search(r"([A-Z][a-z]{2})-(\d{1,2})-(\d{4})", path.name)
        ):  # Portfolio_Positions_Oct-24-2026.csv
            as_of = dt.datetime.strptime(" ".join(m.groups()), "%b %d %Y").date()
        if as_of is None:
            raise ImportError_(f"{path.name}: download date not found (footer or file name)")
        number = self._one_account(path, numbers)
        return _statement(
            self.name,
            self.institution,
            [],
            hint=_hint(number or ""),
            account_kind=" ".join(names),
            valuations=[(as_of, total)],
        )


# ---------------------------------------------------------------- Interactive Brokers
_IBKR_FLOWS = {
    "deposits & withdrawals": TRANSFER,
    "dividends": DIVIDENDS,
    "payment in lieu of dividends": DIVIDENDS,
    "withholding tax": TAXES,
    "interest": INTEREST,
    "broker interest paid": FEES,
    "broker interest received": INTEREST,
    "fees": FEES,
    "other fees": FEES,
}


class IbkrImporter:
    """IBKR Activity Statement CSV: every line is '<Section>,<Header|Data|Total|SubTotal>,...'.
    Uses Account Information (account, base currency, type), Net Asset Value (prior and current
    total -> marks at both ends of the period) and the cash sections in _IBKR_FLOWS. Only lines in
    the base currency are booked; IBKR lists other-currency lines separately and also in the base
    currency is converted at the statement's own 'Base Currency Exchange Rate' (skipped if absent:
    the value marks still include it)."""

    name = "ibkr"
    institution = "IBKR"

    def sniff(self, path: Path, head: str) -> bool:
        return (
            path.suffix.lower() == ".csv"
            and head.lstrip().startswith("Statement,Header,Field Name")
            and "Transaction History,Header" not in head  # that report: IbkrHistoryImporter
        )

    def parse(self, path: Path) -> ParsedStatement:
        rows = read_csv(path)
        headers: dict[str, list[str]] = {}
        info: dict[str, str] = {}
        period: tuple[dt.date, dt.date] | None = None
        nav: tuple[Decimal, Decimal] | None = None
        lines: list[tuple[str, dict[str, str]]] = []
        rates: dict[str, Decimal] = {}
        for row in rows:
            if len(row) < 3:
                continue
            section, kind, rest = row[0], row[1], row[2:]
            if kind == "Header":
                headers[section] = rest
                continue
            rec = dict(zip(headers.get(section, []), rest, strict=False))
            if section == "Statement" and rec.get("Field Name") == "Period":
                period = _ibkr_period(rec.get("Field Value", ""))
            elif section == "Account Information" and kind == "Data":
                info[rec.get("Field Name", "")] = rec.get("Field Value", "")
            elif (
                section == "Net Asset Value"
                and kind == "Data"
                and rec.get("Asset Class") == "Total"
            ):
                prior, current = (
                    money(rec.get("Prior Total", "")),
                    money(rec.get("Current Total", "")),
                )
                if prior is not None and current is not None:
                    nav = (prior, current)
            elif section == "Base Currency Exchange Rate" and kind == "Data":
                if (rate := money(rec.get("Rate", ""))) is not None:
                    rates[rec.get("Currency", "")] = rate
            elif section.lower() in _IBKR_FLOWS and kind == "Data":
                lines.append((section.lower(), rec))
        if period is None:
            raise ImportError_(f"{path.name}: statement period not found")
        base = info.get("Base Currency", "USD") or "USD"
        out: list[RawTxn] = []
        rates[base] = Decimal(1)
        for section, rec in lines:
            rate = rates.get(rec.get("Currency", ""))  # None for 'Total' / 'Total in USD' rows
            date = _ibkr_date(rec.get("Date") or rec.get("Settle Date") or "")
            amount = money(rec.get("Amount", ""))
            if rate is None or date is None or amount is None:
                continue
            if rec.get("Currency") != base:
                amount = (amount * rate).quantize(Decimal("0.01"))
            kind = _IBKR_FLOWS[section]
            desc = rec.get("Description", "")
            if section == "deposits & withdrawals":
                desc = f"{'Deposit' if amount > 0 else 'Withdrawal'} · {desc}"
            if t := flow(date, amount, kind, desc):
                out.append(t)
        marks: list[tuple[dt.date, Decimal]] = []
        if nav is not None:
            marks = [(period[0] - dt.timedelta(days=1), nav[0]), (period[1], nav[1])]
        return _statement(
            self.name,
            self.institution,
            out,
            hint=_hint(info.get("Account", "")),
            account_kind=info.get("Account Type", ""),
            currency=base,
            valuations=marks,
        )


# IBKR's Transaction History report follows the portal language; types are mapped to the English
# action text that classify() understands.
_IBKR_TYPES_ZH = {
    "买": "Buy",
    "卖": "Sell",
    "存款": "Deposit",
    "取款": "Withdrawal",
    "股息": "Dividend",
    "替代支付": "Payment in Lieu of Dividend",
    "外国预扣税": "Foreign Tax Withholding",
    "预扣税": "Withholding Tax",
    "贷记利息": "Credit Interest",
    "借记利息": "Debit Interest",
    "利息": "Interest",
    "其他费用": "Other Fee",
    "佣金调整": "Commission Adjustment",
}


class IbkrHistoryImporter:
    """IBKR "Transaction History" report (Performance & Reports → Transaction History), in the
    portal's language: '<Section>,<Header|Data>,…' lines with a 'Transaction History' section of
    date, account, description, type, symbol, quantity, price, gross, commission and net amount.
    Only cash crossing the account's edge is booked (as for the Activity Statement). The report
    carries no net asset value, so the account's worth needs a valuation or the Flex connector."""

    name = "ibkr_history"
    institution = "IBKR"

    def sniff(self, path: Path, head: str) -> bool:
        return (
            path.suffix.lower() == ".csv"
            and head.lstrip().startswith("Statement,Header")
            and ("Transaction History,Header" in head)
        )

    def parse(self, path: Path) -> ParsedStatement:
        rows = read_csv(path)
        header: list[str] | None = None
        base = "USD"
        out: list[RawTxn] = []
        account = ""
        for row in rows:
            if len(row) < 3:
                continue
            section, kind, rest = row[0], row[1], row[2:]
            if (
                section in ("总结", "Summary")
                and kind == "Data"
                and rest[0] in ("基础货币", "Base Currency")
            ):
                base = (rest[1] if len(rest) > 1 else "") or base
            if section != "Transaction History":
                continue
            if kind == "Header":
                header = rest
                continue
            if kind != "Data" or header is None:
                continue
            c = {
                "date": _col(header, "日期", "date"),
                "account": _col(header, "账户", "account"),
                "desc": _col(header, "说明", "description"),
                "type": _col(header, "交易类型", "transaction type", "type"),
                "symbol": _col(header, "代码", "symbol"),
                "qty": _col(header, "数量", "quantity"),
                "net": _col(header, "净额", "net amount", "net"),
                "gross": _col(header, "总额", "gross amount", "gross"),
            }
            date = _ibkr_date(_cell(rest, c["date"]))
            amount = money(_cell(rest, c["net"])) or money(_cell(rest, c["gross"]))
            if date is None or amount is None:
                continue
            account = account or _cell(rest, c["account"])
            raw_type = _cell(rest, c["type"])
            action = _IBKR_TYPES_ZH.get(raw_type, raw_type)
            symbol = _cell(rest, c["symbol"]).strip("-")
            kind_ = classify(action, money(_cell(rest, c["qty"])), symbol)
            desc = _cell(rest, c["desc"])
            if kind_ == TRANSFER:
                desc = f"{'Deposit' if amount > 0 else 'Withdrawal'} · {desc}"
            if t := flow(
                date,
                amount.quantize(Decimal("0.01")),
                kind_,
                f"{action} · {desc}" if kind_ != TRANSFER else desc,
            ):
                out.append(t)
        if header is None:
            raise ImportError_(f"{path.name}: Transaction History section not found")
        return _statement(
            self.name,
            self.institution,
            out,
            hint=_hint(account, path.name),
            currency=base,
        )


def _ibkr_date(s: str) -> dt.date | None:
    s = s.strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _ibkr_period(s: str) -> tuple[dt.date, dt.date] | None:
    """'January 1, 2026 - September 30, 2026' (or a single day)."""
    parts = [p.strip() for p in s.split(" - ")]
    try:
        dates = [dt.datetime.strptime(p, "%B %d, %Y").date() for p in parts if p]
    except ValueError:
        return None
    if not dates:
        return None
    return dates[0], dates[-1]
