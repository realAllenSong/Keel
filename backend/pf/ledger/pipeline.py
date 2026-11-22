"""Post-import processing: pair internal transfers, recognise your own money moving between your
accounts (wallets, other logins, C2C purchases), categorise, then re-derive anything that
depends on account flows (valuation adjustments, account metadata)."""

from __future__ import annotations

from sqlalchemy import Connection

from pf.ledger.categorize import apply_transfer_overrides, categorize
from pf.ledger.ownflows import match_own_flows
from pf.ledger.transfers import book_card_payments, match_transfers
from pf.ledger.valuation import refresh_valuations


def after_import(conn: Connection, txn_ids: list[int]) -> None:
    match_transfers(conn, txn_ids)
    book_card_payments(conn, txn_ids)  # a card payment seen from the bank's side only, so far
    match_own_flows(conn)
    apply_transfer_overrides(conn)
    categorize(conn, txn_ids)
    from pf.ledger.tags import apply_rules

    apply_rules(conn, _alive(conn, txn_ids))  # tag rules, on the lines that are still single
    refresh_valuations(conn)
    from pf.reports.common import sync_account_meta

    sync_account_meta(conn)


def _alive(conn: Connection, txn_ids: list[int]) -> list[int]:
    """The ids that still exist (pairing transfers replaces two lines by one transaction)."""
    import json

    from sqlalchemy import text

    return [
        r.id
        for r in conn.execute(
            text("SELECT id FROM transactions WHERE id IN (SELECT value FROM json_each(:i))"),
            {"i": json.dumps(txn_ids)},
        )
    ]
