"""Statements written by API connectors (pf/connectors): one JSON file per account per sync,
kept under raw/connectors/<source>/<date>/ so `pf rebuild` replays exactly what was fetched.

    {"keel_connector": 1, "source": "plaid", "institution": "Chase", "account_id": "...",
     "name": "Sapphire", "mask": "0002", "type": "Liabilities", "asset_class": null,
     "currency": "USD", "fetched_at": "2026-10-31T08:00:00Z",
     "balances":     [{"date": "2026-10-31", "amount": "-150.00"}],        # ledger sign
     "valuations":   [{"date": "2026-10-31", "value": "6009.70"}],         # brokers: total value
     "transactions": [{"id": "...", "date": "2026-10-30", "amount": "-12.50",   # ledger sign
                       "description": "...", "payee": null, "category_hint": null,
                       "category": null}],
     "holdings":     [{"symbol": "QQQ", "quantity": "1.2", "value": "620.10", ...}]}

Every connector converts to ledger sign itself (assets: + = money in; liabilities: debt is
negative, a purchase is negative), so this importer only maps fields.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path

from pf.importers.base import ImportError_, ParsedStatement, RawTxn

PREFIX = "conn_"


def _kind(doc: dict) -> str:
    """What the account is, in one word (the app shows it in the reader's language)."""
    name = (doc.get("name") or "").lower()
    if doc["type"] == "Liabilities":
        return "Loan" if re.search(r"loan|mortgage|heloc", name) else "Card"
    return {
        "brokerage": "Brokerage",
        "retirement": "Retirement",
        "crypto": "Exchange",
    }.get(doc.get("asset_class") or "", "Savings" if "saving" in name else "Checking")


# One name per institution whatever the source calls it (SnapTrade 'Schwab', Plaid 'Charles Schwab').
INSTITUTION_NAMES = {
    "schwab": "Charles Schwab",
    "charles schwab": "Charles Schwab",
    "interactive brokers": "IBKR",
    "interactive brokers llc": "IBKR",
    "fidelity investments": "Fidelity",
    "amex": "American Express",
    "bank of america": "Bank of America",
    "jpmorgan chase": "Chase",
}


def institution_name(raw: str) -> str:
    return INSTITUTION_NAMES.get(" ".join(raw.lower().split()), raw.strip())


def _digits(mask: str | None) -> str | None:
    """'XXX130' / '.130' -> '130' (statements saved before masks were cleaned keep working)."""
    d = re.sub(r"\D", "", mask or "")
    return d or None


def suggested_account(doc: dict) -> str:
    """'Liabilities:Chase:Card-0002' — the account a connector creates when nothing matches.
    Short and stable: the institution, what kind of account, and its last digits."""
    inst = institution_name(doc.get("institution") or doc["source"]).replace(":", "-")
    mask = _digits(doc.get("mask"))
    return f"{doc['type']}:{inst}:{_kind(doc)}" + (f"-{mask}" if mask else "")


class ConnectorImporter:
    name = "connector"  # the parsed statement's importer is conn_<source>

    def sniff(self, path: Path, head: str) -> bool:
        return path.suffix.lower() == ".json" and '"keel_connector"' in head

    def parse(self, path: Path) -> ParsedStatement:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ImportError_(f"{path.name}: not a connector statement") from exc
        if doc.get("keel_connector") != 1 or doc.get("type") not in ("Assets", "Liabilities"):
            raise ImportError_(f"{path.name}: unsupported connector statement")
        rows = [
            RawTxn(
                date=dt.date.fromisoformat(t["date"]),
                amount=Decimal(t["amount"]),
                description=t.get("description") or "",
                payee=t.get("payee"),
                ref=t.get("id"),
                category_hint=t.get("category_hint"),
                category=t.get("category"),
                memo=t.get("memo"),
            )
            for t in doc.get("transactions", [])
        ]
        return ParsedStatement(
            importer=PREFIX + doc["source"],
            institution=institution_name(doc.get("institution") or doc["source"]),
            currency=doc.get("currency") or "USD",
            rows=rows,
            account_type=doc["type"],
            asset_class=doc.get("asset_class") or ("cash" if doc["type"] == "Assets" else None),
            assertions=[
                (dt.date.fromisoformat(b["date"]), Decimal(b["amount"]))
                for b in doc.get("balances", [])
            ],
            valuations=[
                (dt.date.fromisoformat(v["date"]), Decimal(v["value"]))
                for v in doc.get("valuations", [])
            ],
            account_hint=_digits(doc.get("mask")),
            default_account=suggested_account(doc),
            holdings=_holdings(doc),
            holdings_as_of=_holdings_day(doc),
            trades=list(doc.get("trades") or []),
            bank_pending=doc.get("pending"),
            bank_current=Decimal(doc["current"]) if doc.get("current") else None,
            bank_available=Decimal(doc["available"]) if doc.get("available") else None,
            in_flight_as_of=doc.get("fetched_at"),
        )


def _holdings_day(doc: dict) -> dt.date | None:
    """The day the positions are for (statements saved before it was written: the day of the
    value mark, else the day they were fetched)."""
    day = doc.get("holdings_as_of") or next(
        (v["date"] for v in reversed(doc.get("valuations") or [])), None
    )
    day = day or str(doc.get("fetched_at") or "")[:10]
    try:
        return dt.date.fromisoformat(day)
    except ValueError:
        return None


def _holdings(doc: dict) -> list[dict] | None:
    """Positions with the cost of the whole position, or None when this statement does not
    report positions (a bank, or a broker whose positions could not be read)."""
    if doc.get("asset_class") not in ("brokerage", "retirement", "crypto"):
        return None
    if "holdings" not in doc or doc.get("holdings_complete") is False:
        return None
    if not doc["holdings"] and "holdings_as_of" not in doc:
        # Saved before positions were marked complete: an empty list beside a value above zero
        # means they were not read, not that the account held nothing.
        worth = [Decimal(str(v["value"])) for v in doc.get("valuations") or []]
        if any(w > 0 for w in worth):
            return None
    out = []
    for h in doc["holdings"]:
        if not h.get("symbol") or h.get("quantity") in (None, ""):
            continue
        qty = Decimal(str(h["quantity"]))
        cost = Decimal(str(h["cost_basis"])) if h.get("cost_basis") not in (None, "") else None
        # SnapTrade reports the average price paid per unit (statements saved before the flag
        # existed too); everyone else the cost of the position.
        per_unit = h.get("cost_basis_per_unit", doc.get("source") == "snaptrade")
        if cost is not None and per_unit:
            cost *= qty
        price = h.get("price")
        if price in (None, "") and h.get("value") not in (None, "") and qty:
            price = str(Decimal(str(h["value"])) / qty)
        out.append(
            {
                "symbol": " ".join(str(h["symbol"]).split()),
                "kind": h.get("kind"),
                "quantity": str(qty),
                "price": str(price) if price not in (None, "") else None,
                "cost_basis": str(cost) if cost is not None else None,
                "currency": h.get("currency") or doc.get("currency") or "USD",
                "cash_equivalent": bool(h.get("cash_equivalent")),
            }
        )
    return out
