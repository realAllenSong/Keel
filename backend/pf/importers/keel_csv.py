"""Keel's own statement file: what an agent writes after reading a statement that no importer
understands (a PDF, a screenshot, a bank with no export), so the lines still go through the
ordinary pipeline: dedup, transfer pairing, categorising, and a balance check that catches a
misread figure.

    # keel statement
    # institution: Chase
    # kind: card            (bank | card; default bank)
    # account: 0003         (last digits; optional)
    # currency: USD         (default USD)
    date,description,amount,balance
    2026-10-02,COFFEE SHOP,-4.50,-104.50
    2026-10-05,PAYMENT THANK YOU,104.50,0.00

Amounts and balances are in ledger sign for that account: money into a bank account is positive,
a card purchase is negative and a card's debt is a negative balance. `balance` is optional
(the balance after the line); with it, Keel checks its own total against the statement's.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pf.importers.base import ImportError_, ParsedStatement, RawTxn

MARK = "# keel statement"


class KeelCsvImporter:
    name = "keel_csv"

    def sniff(self, path: Path, head: str) -> bool:
        return head.lstrip("﻿").lower().startswith(MARK)

    def parse(self, path: Path) -> ParsedStatement:
        text = path.read_text(encoding="utf-8-sig")
        meta: dict[str, str] = {}
        body: list[str] = []
        for line in text.splitlines():
            if line.startswith("#"):
                key, sep, value = line[1:].partition(":")
                if sep:
                    meta[key.strip().lower()] = value.strip()
            elif line.strip():
                body.append(line)
        rows = list(csv.DictReader(io.StringIO("\n".join(body))))
        if not rows:
            raise ImportError_(f"{path.name}: no lines under the header")
        cols = {c.strip().lower(): c for c in rows[0]}
        missing = [c for c in ("date", "description", "amount") if c not in cols]
        if missing:
            raise ImportError_(
                f"{path.name}: missing column {missing[0]} (need date, description, amount)"
            )

        def num(row: dict, col: str, n: int) -> Decimal:
            raw = (
                (row.get(cols[col]) or "")
                .replace(",", "")
                .replace("$", "")
                .replace("¥", "")
                .strip()
            )
            try:
                return Decimal(raw)
            except InvalidOperation as exc:
                raise ImportError_(f"{path.name}: line {n}: {col} {raw!r} is not a number") from exc

        out: list[RawTxn] = []
        balances: dict[dt.date, Decimal] = {}
        for n, row in enumerate(rows, start=1):
            try:
                day = dt.date.fromisoformat((row.get(cols["date"]) or "").strip())
            except ValueError as exc:
                raise ImportError_(f"{path.name}: line {n}: date must be YYYY-MM-DD") from exc
            after = None
            if "balance" in cols and (row.get(cols["balance"]) or "").strip():
                after = num(row, "balance", n)
                balances[day] = after  # the last line of a day carries that day's closing balance
            out.append(
                RawTxn(
                    date=day,
                    amount=num(row, "amount", n),
                    description=(row.get(cols["description"]) or "").strip(),
                    payee=(row.get(cols["payee"]) or "").strip() or None
                    if "payee" in cols
                    else None,
                    balance_after=after,
                )
            )
        card = meta.get("kind", "bank").lower() in ("card", "credit", "credit card", "liability")
        institution = meta.get("institution") or "Bank"
        digits = re.sub(r"\D", "", meta.get("account", ""))[-4:]
        kind = "Card" if card else "Checking"
        return ParsedStatement(
            importer=self.name,
            institution=institution,
            currency=(meta.get("currency") or "USD").upper(),
            rows=out,
            account_type="Liabilities" if card else "Assets",
            asset_class=None if card else "cash",
            assertions=sorted(balances.items()),
            account_hint=digits or None,
            default_account=f"{'Liabilities' if card else 'Assets'}:{institution.replace(':', '-')}:{kind}"
            + (f"-{digits}" if digits else ""),
        )
