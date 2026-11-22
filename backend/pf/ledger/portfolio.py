"""What a brokerage account holds and what was traded in it, kept beside the ledger.

The ledger books a brokerage account as one value (marks) plus the cash crossing its edge, so
net worth and income never depend on this. A connector statement also carries the broker's
positions for the day and its trade events; `record` stores them when the statement is imported,
and `pf rebuild` refills both tables by replaying the same raw statements."""

from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, text

from pf.importers.base import ParsedStatement

log = logging.getLogger(__name__)


def record(conn: Connection, account_id: int, file_id: int | None, stmt: ParsedStatement) -> None:
    if stmt.holdings is not None and stmt.holdings_as_of is not None:
        _snapshot(conn, account_id, file_id, stmt.holdings_as_of, stmt.holdings)
    for t in stmt.trades:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO trades(account_id, date, action, symbol, underlying, kind,"
                " quantity, price, amount, fee, currency, description, ref, import_file_id)"
                " VALUES (:a, :date, :action, :symbol, :underlying, :kind, :quantity, :price,"
                " :amount, :fee, :currency, :description, :ref, :f)"
            ),
            {
                "a": account_id,
                "f": file_id,
                "date": t["date"],
                "action": t["action"],
                "symbol": t["symbol"],
                "underlying": t.get("underlying"),
                "kind": t.get("kind"),
                "quantity": str(t.get("quantity") or "0"),
                "price": t.get("price"),
                "amount": str(t.get("amount") or "0"),
                "fee": t.get("fee"),
                "currency": t.get("currency") or stmt.currency,
                "description": t.get("description"),
                "ref": str(t["id"]),
            },
        )


def _snapshot(
    conn: Connection, account_id: int, file_id: int | None, day: dt.date, holdings: list[dict]
) -> None:
    """The broker's positions on `day`: a later report for the same day replaces the earlier
    one, and an empty list is a fact too (the account holds nothing)."""
    key = {"a": account_id, "d": day.isoformat()}
    conn.execute(text("DELETE FROM holdings WHERE account_id = :a AND as_of = :d"), key)
    conn.execute(text("DELETE FROM holding_snapshots WHERE account_id = :a AND as_of = :d"), key)
    conn.execute(
        text(
            "INSERT INTO holding_snapshots(account_id, as_of, import_file_id) VALUES (:a, :d, :f)"
        ),
        {**key, "f": file_id},
    )
    merged: dict[str, dict] = {}
    for h in holdings:  # the same symbol twice (two lots, two sub-accounts) is one position
        m = merged.get(h["symbol"])
        if m is None:
            merged[h["symbol"]] = dict(h)
            continue
        m["quantity"] = str(Decimal(m["quantity"]) + Decimal(h["quantity"]))
        if m.get("cost_basis") is not None and h.get("cost_basis") is not None:
            m["cost_basis"] = str(Decimal(m["cost_basis"]) + Decimal(h["cost_basis"]))
        else:
            m["cost_basis"] = None
    for h in merged.values():
        conn.execute(
            text(
                "INSERT INTO holdings(account_id, as_of, symbol, kind, quantity, price,"
                " cost_basis, currency, cash_equivalent)"
                " VALUES (:a, :d, :symbol, :kind, :quantity, :price, :cost, :currency, :cash)"
            ),
            {
                **key,
                "symbol": h["symbol"],
                "kind": h.get("kind"),
                "quantity": h["quantity"],
                "price": h.get("price"),
                "cost": h.get("cost_basis"),
                "currency": h["currency"],
                "cash": 1 if h.get("cash_equivalent") else 0,
            },
        )


def backfill(conn: Connection, raw_dir: Path) -> int:
    """Positions from statements imported before these tables existed (their raw files are
    already in the ledger, so the importer skips them). Runs once: only while no snapshot is
    stored. Returns how many statements it read."""
    if conn.execute(text("SELECT 1 FROM holding_snapshots LIMIT 1")).first():
        return 0
    from pf.importers.connector_json import ConnectorImporter

    done = 0
    rows = conn.execute(
        text(
            "SELECT id, account_id, raw_path FROM import_files"
            " WHERE importer = 'connector' AND account_id IS NOT NULL ORDER BY id"
        )
    ).all()
    for r in rows:
        path = raw_dir / r.raw_path
        try:
            if '"holdings"' not in path.read_text(encoding="utf-8"):
                continue
            stmt = ConnectorImporter().parse(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if stmt.holdings is None and not stmt.trades:
            continue
        record(conn, r.account_id, r.id, stmt)
        done += 1
    if done:
        log.info("Portfolio backfill: positions read from %s earlier statements", done)
    return done
