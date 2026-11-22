"""Importer contract: every institution parser turns a file into a ParsedStatement."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol


@dataclass
class RawTxn:
    date: dt.date
    amount: Decimal  # signed in LEDGER terms for the statement account (+ = debit)
    description: str
    payee: str | None = None
    ref: str | None = None  # institution's own transaction id, if any
    category_hint: str | None = None  # e.g. Chase's own category column
    balance_after: Decimal | None = None
    memo: str | None = None  # kept as the transaction note (e.g. wallet payment method)
    # A category the statement states outright (a broker's dividend, interest, fee or tax line):
    # booked as confirmed instead of waiting in review.
    category: str | None = None


@dataclass
class ParsedStatement:
    importer: str
    institution: str
    currency: str
    rows: list[RawTxn]
    account_type: str = "Assets"  # Assets | Liabilities
    asset_class: str | None = "cash"
    # (date, balance) pairs in ledger sign, i.e. card debt is negative
    assertions: list[tuple[dt.date, Decimal]] = field(default_factory=list)
    account_hint: str | None = None  # e.g. last 4 digits found in the file
    default_account: str | None = None  # one fixed account per source (wallets): no question asked
    flow_only: bool = (
        False  # income/spending count, the balance is not money held (net worth skips it)
    )
    # (date, account value) marks from a NAV report or an observed closing balance.
    valuations: list[tuple[dt.date, Decimal]] = field(default_factory=list)
    # The statement's account holder (a wallet export's header): transfers to this name are you.
    holder: str | None = None
    # A broker's positions on one day (None = not reported; [] = reported, holds nothing) and
    # the trades it lists. Kept beside the ledger, see pf/ledger/portfolio.py.
    holdings: list[dict[str, Any]] | None = None
    holdings_as_of: dt.date | None = None
    trades: list[dict[str, Any]] = field(default_factory=list)

    @property
    def period(self) -> tuple[dt.date, dt.date] | None:
        dates = [r.date for r in self.rows] + [d for d, _ in self.valuations]
        if not dates:
            return None
        return min(dates), max(dates)


class Importer(Protocol):
    name: str

    def sniff(self, path: Path, head: str) -> bool:
        """Cheap check whether this importer understands the file. `head` = first ~8KB decoded."""
        ...

    def parse(self, path: Path) -> ParsedStatement: ...


class ImportError_(ValueError):
    """Raised when a file cannot be parsed or bound to an account."""


def read_head(path: Path, n: int = 8192) -> str:
    with path.open("rb") as stream:
        raw = stream.read(n)
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")
