"""OKX: funding / trading account bills and an asset-balance snapshot.

Like a brokerage (see brokers.py), the exchange is ONE account valued in USD: the ledger has no
coin prices, so the account's value comes from the snapshot's USD valuation, and the bills only
contribute money crossing the account's edge:

  stablecoin (USDT, USDC, ...) and USD deposits / withdrawals  -> uncategorised, pairs as transfers
  C2C / P2P stablecoin buys and sells                           -> same; the fiat side is paid from
                                                                   a wallet or card (see ownflows)
  stablecoin interest / Earn rewards                            -> Income:Interest
  stablecoin fees on those lines                                -> Expenses:Fees:Investment
Stablecoins are taken at $1. Trades, converts and transfers between OKX's own accounts move value
inside the account and are skipped, as are deposits and withdrawals of other coins: without a
price their dollar value is unknown, so the next snapshot's mark carries them.

Column names (English and Chinese exports) are unverified until checked against a real file.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

from pf.importers.base import ImportError_, ParsedStatement, RawTxn
from pf.importers.brokers import FEES, INTEREST, money
from pf.importers.cn_wallets import read_table

STABLE = {"USD", "USDT", "USDC", "DAI", "FDUSD", "PYUSD", "USDG", "TUSD"}
TIME = ("time", "时间", "日期", "date")
TYPE = ("type", "bill type", "类型", "账单类型")
AMOUNT = ("amount", "quantity", "数量", "金额", "变动数量")
CCY = ("currency", "symbol", "unit", "coin", "币种")
FEE = ("fee", "手续费")
REF = ("bill id", "id", "账单id", "账单编号")
VALUE = ("usd value", "value (usd)", "valuation (usd)", "估值(usd)", "估值（usd）", "折合usd")


def _find(header: list[str], names: tuple[str, ...]) -> int | None:
    low = [h.strip().lower() for h in header]
    for n in names:
        if n in low:
            return low.index(n)
    return None


def _when(s: str) -> dt.date | None:
    s = s.strip()
    if re.fullmatch(r"\d{13}", s):  # epoch milliseconds
        return dt.datetime.fromtimestamp(int(s) / 1000, dt.UTC).date()
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _kind(t: str) -> str | None:
    t = t.lower()
    if re.search(r"transfer|划转", t):
        return None  # between OKX's own accounts
    if re.search(r"c2c|p2p|快捷|买币|卖币|法币|buy crypto|sell crypto", t):
        return "out" if re.search(r"sell|卖", t) else "in"  # paid / got paid outside, in fiat
    if re.search(r"deposit|充值|充币|入金", t):
        return "in"
    if re.search(r"withdraw|提现|提币|出金", t):
        return "out"
    if re.search(r"interest|earn|savings|利息|收益|赚币", t):
        return "interest"
    return None


class OkxImporter:
    name = "okx"
    institution = "OKX"

    def _table(self, path: Path) -> tuple[list[list[str]], int, str] | None:
        rows = read_table(path)
        for i, row in enumerate(rows[:20]):
            if _find(row, CCY) is None:
                continue
            if (
                _find(row, TIME) is not None
                and _find(row, TYPE) is not None
                and _find(row, AMOUNT) is not None
            ):
                return rows, i, "bills"
            if _find(row, VALUE) is not None:
                return rows, i, "snapshot"
        return None

    def sniff(self, path: Path, head: str) -> bool:
        if path.suffix.lower() not in (".csv", ".xlsx"):
            return False
        marked = "okx" in path.name.lower() or "okx" in head.lower()
        if path.suffix.lower() == ".csv" and not marked:
            return False
        try:
            found = self._table(path)
        except Exception:
            return False
        if found is None:
            return False
        if not marked:  # xlsx: look for the marker inside the sheet
            return any("okx" in " ".join(r).lower() for r in found[0][: found[1] + 1])
        return True

    def parse(self, path: Path) -> ParsedStatement:
        found = self._table(path)
        if found is None:
            raise ImportError_(f"{path.name}: 找不到 OKX 账单或资产表的表头")
        rows, h, kind = found
        if kind == "snapshot":
            return self._snapshot(path, rows, h)
        header = rows[h]
        c = {
            "time": _find(header, TIME),
            "type": _find(header, TYPE),
            "amount": _find(header, AMOUNT),
            "ccy": _find(header, CCY),
            "fee": _find(header, FEE),
            "ref": _find(header, REF),
        }

        def get(row: list[str], key: str) -> str:
            i = c[key]
            return row[i] if i is not None and i < len(row) else ""

        out: list[RawTxn] = []
        for row in rows[h + 1 :]:
            date = _when(get(row, "time"))
            amount = money(get(row, "amount"))
            ccy = get(row, "ccy").upper()
            kind_ = _kind(get(row, "type"))
            if date is None or amount is None or kind_ is None or ccy not in STABLE:
                continue
            ref = get(row, "ref") or None
            if kind_ == "in":
                amount = abs(amount)
            elif kind_ == "out":
                amount = -abs(amount)
            desc = f"OKX {get(row, 'type')} {ccy}"
            if amount:
                out.append(
                    RawTxn(
                        date=date,
                        amount=amount,
                        description=desc,
                        ref=ref,
                        category=INTEREST if kind_ == "interest" else None,
                    )
                )
            fee = money(get(row, "fee"))
            if fee:
                out.append(
                    RawTxn(
                        date=date,
                        amount=-abs(fee),
                        description=f"{desc} fee",
                        ref=f"{ref}-fee" if ref else None,
                        category=FEES,
                    )
                )
        return self._statement(out, [])

    def _snapshot(self, path: Path, rows: list[list[str]], h: int) -> ParsedStatement:
        value = _find(rows[h], VALUE)
        total = Decimal(0)
        for row in rows[h + 1 :]:
            v = money(row[value]) if value is not None and value < len(row) else None
            if v is not None:
                total += v
        as_of = None
        for row in rows[:h]:
            if as_of := next((d for d in map(_when, row) if d), None):
                break
        if as_of is None and (m := re.search(r"(\d{4})-?(\d{2})-?(\d{2})", path.name)):
            as_of = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if as_of is None:
            raise ImportError_(f"{path.name}: 找不到资产快照的日期（表头上方或文件名里）")
        return self._statement([], [(as_of, total.quantize(Decimal("0.01")))])

    def _statement(
        self, rows: list[RawTxn], valuations: list[tuple[dt.date, Decimal]]
    ) -> ParsedStatement:
        return ParsedStatement(
            importer=self.name,
            institution=self.institution,
            currency="USD",
            rows=rows,
            account_type="Assets",
            asset_class="crypto",
            default_account="Assets:OKX:Crypto",
            valuations=valuations,
        )
