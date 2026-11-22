"""Rename a ledger account everywhere its name is stored as text (postings refer to the id).

`tidy_connector_names` renames accounts that connectors created under older, longer names
('Liabilities:Bank of America:Customized Cash Rewards Visa Signature-0006') to the current
scheme ('Liabilities:Bank of America:Card-0006'), using the latest statement each was fed.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Connection, text

from pf.importers.connector_json import suggested_account
from pf.settings import get_settings

# (table, column) pairs that hold an account's full name
_NAME_COLUMNS = (
    ("valuations", "account_name"),
    ("overrides", "account_name"),
    ("overrides", "target_account"),
    ("account_bindings", "account_name"),
    ("line_edits", "account_name"),
    ("rules", "target_account"),
    ("rules", "account_filter"),
)


def rename_account(conn: Connection, old: str, new: str) -> None:
    if old == new:
        return
    if old.split(":", 1)[0] != new.split(":", 1)[0]:
        raise ValueError(f"{new} must stay a {old.split(':', 1)[0]} account")
    if conn.execute(text("SELECT 1 FROM accounts WHERE name = :n"), {"n": new}).first():
        raise ValueError(f"{new} already exists")
    if not conn.execute(
        text("UPDATE accounts SET name = :new WHERE name = :old"), {"new": new, "old": old}
    ).rowcount:
        raise ValueError(f"no account {old}")
    for table, column in _NAME_COLUMNS:
        conn.execute(
            text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"),
            {"new": new, "old": old},
        )


def tidy_connector_names(conn: Connection) -> list[tuple[str, str]]:
    """Rename connector-fed accounts to the current naming scheme; returns (old, new) pairs."""
    raw = get_settings().raw_dir
    latest = conn.execute(
        text(
            """SELECT a.name, f.raw_path FROM import_files f JOIN accounts a ON a.id = f.account_id
               WHERE f.importer = 'connector'
                 AND f.id = (SELECT MAX(id) FROM import_files g
                             WHERE g.account_id = f.account_id AND g.importer = 'connector')"""
        )
    ).all()
    done = []
    for name, raw_path in latest:
        doc = json.loads(Path(raw / raw_path).read_text(encoding="utf-8"))
        new = suggested_account(doc)
        taken = conn.execute(text("SELECT 1 FROM accounts WHERE name = :n"), {"n": new}).first()
        if new != name and not taken:
            rename_account(conn, name, new)
            done.append((name, new))
    return done
