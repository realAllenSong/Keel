"""Config-driven CSV importer. Most US bank/card exports only need a YAML profile.

Profile keys (see profiles/*.yaml):
  id, institution, currency, account_type (Assets|Liabilities), asset_class
  detect.header: column names that must all appear in the header row
  detect.filename: optional regex the filename must match
  encoding: default utf-8-sig
  columns: date, description, amount | (debit + credit), [payee, ref, category_hint, balance]
  date_format: strptime format
  sign: as_is | negate   (how the file's amount maps to ledger sign for this account)
  account_hint_regex: regex over the filename whose group(1) identifies the account (e.g. last 4)
  account_hint_preamble_regex: same, over the lines above the header (e.g. a masked card number)
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from pf.i18n import tr
from pf.importers.base import ImportError_, ParsedStatement, RawTxn
from pf.importers.preview import read_table, text_table
from pf.money import parse_decimal


@dataclass
class Profile:
    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    def col(self, key: str) -> str | None:
        return self.raw.get("columns", {}).get(key)


class GenericCsvImporter:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.name = profile.id

    # ---- detection ----
    def sniff(self, path: Path, head: str) -> bool:
        p = self.profile.raw
        fn_re = p.get("detect", {}).get("filename")
        if fn_re and not re.search(fn_re, path.name, re.I):
            return False
        try:
            if path.suffix.lower() in (".xlsx", ".xls"):
                if p.get("format") != "xlsx":
                    return False
                table, _ = read_table(path, limit=60)
            else:
                if p.get("format") == "xlsx":
                    return False
                table = text_table(head, limit=60)
            return self._find_header_cells(table) is not None
        except (ValueError, OSError, UnicodeError, csv.Error, ImportError):
            return False

    def _find_header_cells(self, table: list[list[str]]) -> int | None:
        need = [h.strip().lower() for h in self.profile.raw["detect"]["header"]]
        for i, row in enumerate(table[:60]):
            if all(h in [c.strip().lower() for c in row] for h in need):
                return i
        return None

    # ---- parsing ----
    def parse(self, path: Path) -> ParsedStatement:
        p = self.profile.raw
        table, _ = read_table(path)
        h = self._find_header_cells(table)
        if h is None:
            raise ImportError_(f"{path.name}: " + tr("找不到格式 {name} 的表头", name=self.name))
        header = table[h]
        mapped = [c for c in p["columns"].values() if c]
        if p.get("strict_dates") and any(c not in header for c in mapped):
            raise ImportError_(tr("所选列不存在，请检查列映射"))
        reader = [dict(zip(header, row, strict=False)) for row in table[h + 1 :]]

        sign = Decimal(-1) if p.get("sign") == "negate" else Decimal(1)
        rows: list[RawTxn] = []
        for rec in reader:
            rec = {k.strip(): (v or "").strip() for k, v in rec.items() if k}
            date_s = rec.get(self.profile.col("date") or "", "")
            if not date_s:
                money_columns = ("amount", "debit", "credit")
                if p.get("strict_dates") and any(self._opt(rec, key) for key in money_columns):
                    raise ImportError_(tr("交易日期为空，请检查日期列"))
                continue  # footer / blank line
            try:
                date = dt.datetime.strptime(date_s, p["date_format"]).date()
            except ValueError as exc:
                if p.get("strict_dates"):
                    raise ImportError_(
                        tr("日期无法解析：{v}，请检查日期列和格式", v=date_s)
                    ) from exc
                continue  # summary rows interleaved by some banks
            amount = self._amount(rec) * sign
            balance = self._opt_decimal(rec, "balance")
            if not amount.is_finite() or (balance is not None and not balance.is_finite()):
                raise ImportError_(tr("金额和余额必须是有限数值，请检查列映射"))
            rows.append(
                RawTxn(
                    date=date,
                    amount=amount,
                    description=rec.get(self.profile.col("description") or "", ""),
                    payee=self._opt(rec, "payee"),
                    ref=self._opt(rec, "ref"),
                    category_hint=self._opt(rec, "category_hint"),
                    balance_after=balance * sign if balance is not None else None,
                )
            )

        if not rows:
            raise ImportError_(tr("没有可解析的交易行，请检查列映射和日期格式"))
        hint = None
        if hint_re := p.get("account_hint_regex"):
            m = re.search(hint_re, path.name)
            hint = m.group(1) if m else None
        if hint is None and (pre_re := p.get("account_hint_preamble_regex")):
            m = re.search(pre_re, "\n".join(" ".join(r) for r in table[:h]))
            hint = m.group(1) if m else None

        return ParsedStatement(
            importer=self.name,
            institution=p["institution"],
            currency=p.get("currency", "USD"),
            rows=rows,
            account_type=p.get("account_type", "Assets"),
            asset_class=p.get("asset_class", "cash"),
            assertions=_closing_assertion(rows, p.get("order", "auto")),
            account_hint=hint,
        )

    def _amount(self, rec: dict[str, str]) -> Decimal:
        if col := self.profile.col("amount"):
            return parse_decimal(rec[col] or "0")
        debit = parse_decimal(rec.get(self.profile.col("debit") or "", "") or "0")
        credit = parse_decimal(rec.get(self.profile.col("credit") or "", "") or "0")
        return credit - abs(debit)

    def _opt(self, rec: dict[str, str], key: str) -> str | None:
        col = self.profile.col(key)
        val = rec.get(col, "") if col else ""
        return val or None

    def _opt_decimal(self, rec: dict[str, str], key: str) -> Decimal | None:
        val = self._opt(rec, key)
        return parse_decimal(val) if val else None


def _closing_assertion(rows: list[RawTxn], order: str = "auto") -> list[tuple[dt.date, Decimal]]:
    """Closing balance from a running-balance column.

    Row order can't be inferred from dates alone (many rows share a day), so the chronological
    order is the one where every running balance = previous balance + amount. If that is
    ambiguous or inconsistent we assert nothing: no assertion beats a wrong one."""
    seq = [r for r in rows if r.balance_after is not None]
    if not seq:
        return []

    def chain_ok(s: list[RawTxn]) -> bool:
        return all(
            s[i].balance_after - s[i - 1].balance_after == s[i].amount  # type: ignore[operator]
            and s[i].date >= s[i - 1].date
            for i in range(1, len(s))
        )

    if order == "newest_first":
        candidates = [seq[::-1]]
    elif order == "oldest_first":
        candidates = [seq]
    else:
        candidates = [c for c in (seq, seq[::-1]) if chain_ok(c)]
    closings = {(c[-1].date, c[-1].balance_after) for c in candidates}
    if len(closings) != 1:
        return []
    date, bal = closings.pop()
    if date != max(r.date for r in seq) or bal is None:
        return []
    return [(date, bal)]


@cache
def builtin_profiles() -> list[Profile]:
    out: list[Profile] = []
    for f in sorted(resources.files("pf.importers.profiles").iterdir(), key=lambda x: x.name):
        if f.name.endswith(".yaml"):
            out.append(Profile(yaml.safe_load(f.read_text(encoding="utf-8"))))
    return out


def load_profiles(extra_dir: Path | None = None) -> list[Profile]:
    profiles = list(builtin_profiles())
    if extra_dir and extra_dir.is_dir():
        for f in sorted(extra_dir.glob("*.yaml")):
            profiles.append(Profile(yaml.safe_load(f.read_text(encoding="utf-8"))))
    return profiles
