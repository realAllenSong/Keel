"""Read tabular exports once so preview, detection and parsing agree on columns."""

from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path
from zipfile import BadZipFile

from pydantic import BaseModel, Field

from pf.i18n import tr
from pf.money import parse_decimal

DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m/%d/%y",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y年%m月%d日",
    "%Y%m%d",
)
DATE_NAMES = ("date", "日期", "交易时间", "时间")
AMOUNT_NAMES = ("amount", "金额", "收支", "debit", "credit", "支出", "收入", "借方", "贷方")


class ColumnGuesses(BaseModel):
    date: str | None = None
    description: str | None = None
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    balance: str | None = None
    date_formats: list[str] = Field(default_factory=list)


class StatementPreview(BaseModel):
    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    header_row_index: int | None = None
    encoding: str | None = None
    guesses: ColumnGuesses = Field(default_factory=ColumnGuesses)


def read_table(path: Path, limit: int | None = None) -> tuple[list[list[str]], str]:
    if path.suffix.lower() in (".xlsx", ".xls"):
        import pandas as pd
        from xlrd.biffh import XLRDError

        try:
            frame = pd.read_excel(path, header=None, dtype=str, keep_default_na=False, nrows=limit)
        except (ValueError, BadZipFile, XLRDError) as exc:
            raise ValueError(tr("无法读取 Excel 文件，请检查文件是否完整")) from exc
        return [[str(v).strip() for v in row] for row in frame.values.tolist()], "excel"
    raw = path.read_bytes()
    encoding = "utf-8-sig"
    try:
        body = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = "gb18030"
        body = raw.decode(encoding)
    return text_table(body, limit), encoding


def text_table(body: str, limit: int | None = None) -> list[list[str]]:
    # Preambles often contain a single cell; sniff only multi-column lines.
    sample = "\n".join(
        line for line in body.splitlines()[:80] if any(c in line for c in (",", "\t", ";"))
    )
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        delimiter = "\t" if "\t" in sample else ","
    reader = csv.reader(io.StringIO(body), delimiter=delimiter)
    rows: list[list[str]] = []
    for row in reader:
        rows.append([v.strip() for v in row])
        if limit is not None and len(rows) >= limit:
            break
    return rows


def _date(value: str) -> bool:
    for fmt in DATE_FORMATS:
        try:
            dt.datetime.strptime(value, fmt)
            return True
        except ValueError:
            pass
    return False


def _number(value: str) -> bool:
    try:
        return bool(value) and parse_decimal(value).is_finite()
    except ValueError:
        return False


def _named(header: list[str], names: tuple[str, ...]) -> str | None:
    return next((h for h in header if any(n in h.lower() for n in names)), None)


def looks_like_statement(path: Path) -> bool:
    # The format may use dates we do not yet understand; names alone qualify for teaching.
    try:
        rows, _ = read_table(path, limit=60)
    except (ValueError, OSError, UnicodeError, csv.Error, ImportError):
        return False
    return any(_named(row, DATE_NAMES) and _named(row, AMOUNT_NAMES) for row in rows)


def build_preview(path: Path) -> StatementPreview:
    try:
        table, encoding = read_table(path, limit=80)
    except (ValueError, OSError, UnicodeError, csv.Error, ImportError):
        return StatementPreview()
    for i, header in enumerate(table[:60]):
        if sum(bool(c) for c in header) < 3:
            continue
        later = [r for r in table[i + 1 :] if any(r)]
        if not any(any(_date(c) for c in r) and any(_number(c) for c in r) for r in later):
            continue
        rows = later[:15]
        date = _named(header, DATE_NAMES)
        if date is None:
            date = next(
                (
                    h
                    for j, h in enumerate(header)
                    if h and any(j < len(r) and _date(r[j]) for r in rows)
                ),
                None,
            )
        guesses = ColumnGuesses(
            date=date,
            description=_named(
                header, ("description", "payee", "交易对方", "摘要", "说明", "商户")
            ),
            amount=_named(header, ("amount", "金额", "收支")),
            debit=_named(header, ("debit", "支出", "借方")),
            credit=_named(header, ("credit", "收入", "贷方")),
            balance=_named(header, ("balance", "余额", "running bal")),
        )
        if date:
            j = header.index(date)
            samples = [r[j] for r in rows if j < len(r) and r[j]]
            for fmt in DATE_FORMATS:
                try:
                    if samples:
                        for value in samples:
                            dt.datetime.strptime(value, fmt)
                        guesses.date_formats.append(fmt)
                except ValueError:
                    pass
        return StatementPreview(
            header=header, rows=rows, header_row_index=i, encoding=encoding, guesses=guesses
        )
    return StatementPreview(encoding=encoding)
