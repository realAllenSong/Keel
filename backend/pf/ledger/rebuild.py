"""Rebuild the whole ledger from raw/ + user decisions (rules, overrides, manual entries).

Everything derived from statements is deleted and re-imported in a single DB transaction; if any
file fails the old ledger stays untouched."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, text

from pf.ledger.importing import ImportReport, import_file
from pf.settings import get_settings


@dataclass
class FileSpec:
    raw_path: str
    importer: str
    account: str | None


def rebuild(conn: Connection) -> list[ImportReport]:
    raw_dir = get_settings().raw_dir
    known = [
        FileSpec(r.raw_path, r.importer, r.account)
        for r in conn.execute(
            text(
                "SELECT f.raw_path, f.importer, a.name AS account FROM import_files f"
                " LEFT JOIN accounts a ON a.id = f.account_id ORDER BY f.id"
            )
        )
    ]
    known_paths = {f.raw_path for f in known}
    extra = [
        FileSpec(str(p.relative_to(raw_dir)), "", None)
        for p in sorted(raw_dir.rglob("*"))
        if p.is_file()
        and not p.name.startswith(".")
        and str(p.relative_to(raw_dir)) not in known_paths
    ]

    # Derived = anything that came from a statement line (or the auto opening balance).
    # Snapshot the ids first: once postings are gone, "has a statement line" can't be evaluated.
    conn.execute(text("DROP TABLE IF EXISTS temp._derived"))
    conn.execute(
        text(
            "CREATE TEMP TABLE _derived AS SELECT id FROM transactions"
            " WHERE import_file_id IS NOT NULL OR kind = 'opening'"
            " OR id IN (SELECT txn_id FROM postings WHERE source_key IS NOT NULL)"
        )
    )
    in_derived = "id IN (SELECT id FROM temp._derived)"
    conn.execute(text(f"UPDATE transactions SET sealed = 0 WHERE {in_derived}"))
    conn.execute(text("DELETE FROM postings WHERE txn_id IN (SELECT id FROM temp._derived)"))
    conn.execute(text(f"DELETE FROM transactions WHERE {in_derived}"))
    conn.execute(text("DROP TABLE temp._derived"))
    conn.execute(text("DELETE FROM balance_assertions WHERE import_file_id IS NOT NULL"))
    for table in ("trades", "holdings", "holding_snapshots"):  # refilled by the replay
        conn.execute(text(f"DELETE FROM {table}"))
    conn.execute(text("DELETE FROM import_files"))

    reports = []
    for spec in [*known, *extra]:
        path = Path(raw_dir / spec.raw_path)
        reports.append(
            import_file(
                conn,
                path,
                # Connector statements choose their account again (they carry the account's
                # digits), so a fixed matching rule repairs history on rebuild.
                account=None if spec.importer == "connector" else spec.account,
                importer=spec.importer or None,
                archive=True,
            )
        )
    from pf.ledger.valuation import refresh_valuations

    refresh_valuations(conn)
    return reports
