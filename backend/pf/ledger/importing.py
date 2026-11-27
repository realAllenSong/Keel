"""Import pipeline: file -> parse -> bind account -> dedup -> post -> assertions -> opening
balance -> categorise -> reconcile. One file is one atomic DB transaction."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, text

from pf.i18n import tr
from pf.importers.archive import extract_statement, is_locked, statement_zip
from pf.importers.archive_passwords import ArchivePasswords, provider_for_member
from pf.importers.base import ImportError_, ParsedStatement
from pf.importers.preview import StatementPreview, build_preview
from pf.importers.registry import detect, get_importer
from pf.ledger.accounts_config import load_specs, resolve_account
from pf.ledger.core import (
    OPENING_BALANCES,
    NewPosting,
    NewTxn,
    delete_txn,
    ensure_account,
    post,
)
from pf.ledger.dedup import source_keys
from pf.ledger.edits import apply_to_row, load_edits, post_line
from pf.ledger.reconcile import Discrepancy, reconcile
from pf.money import Commodities, from_minor, to_minor
from pf.settings import get_settings


@dataclass
class ImportReport:
    file: str
    status: str = "imported"  # imported | already_imported | needs_account | unknown_format
    importer: str | None = None
    account: str | None = None
    period: tuple[str, str] | None = None
    new: int = 0
    duplicates: int = 0
    txn_ids: list[int] = field(default_factory=list)
    opening_balance: str | None = None
    discrepancies: list[Discrepancy] = field(default_factory=list)
    message: str | None = None
    pending_sha: str | None = None
    suggested_account: str | None = None
    preview: StatementPreview | None = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def import_file(
    conn: Connection,
    path: Path,
    *,
    account: str | None = None,
    importer: str | None = None,
    archive: bool = True,
    password: str | None = None,
) -> ImportReport:
    report = ImportReport(file=path.name)
    digest = sha256_file(path)
    if conn.execute(text("SELECT 1 FROM import_files WHERE sha256 = :h"), {"h": digest}).first():
        report.status = "already_imported"
        return report
    if is_locked(path):
        return _import_archive(conn, path, digest, report, account=account, password=password)

    pending = conn.execute(
        text("SELECT filename, hint FROM pending_imports WHERE sha256=:h"), {"h": digest}
    ).first()
    if pending:
        report.file = pending.filename
    try:
        imp = get_importer(importer, conn) if importer else detect(path, conn)
    except ImportError_:
        if importer:
            raise
        # Only keep files that could become a statement once taught; obvious non-statements
        # (no date/amount columns) are rejected now instead of cluttering the pending list.
        from pf.importers.preview import looks_like_statement

        if not looks_like_statement(path):
            raise ImportError_(
                f"{path.name}: no importer recognises this file, and it does not look like a"
                " statement (no date and amount columns)"
            ) from None
        return _pend(conn, path, digest, report, None)
    stmt = imp.parse(path)
    if pending and pending.hint:
        stmt.account_hint = pending.hint
    report.importer = imp.name
    account = (
        account
        or resolve_account(stmt, load_specs(), conn)
        or _same_account(conn, stmt)
        or stmt.default_account
    )
    if not account:
        return _pend(conn, path, digest, report, stmt)
    if not account.startswith(stmt.account_type + ":"):
        raise ImportError_(f"{account} is not a {stmt.account_type} account ({imp.name})")
    report.account = account

    prec = Commodities(conn).precision(stmt.currency, "currency")
    acc_id = ensure_account(
        conn,
        account,
        currency=stmt.currency,
        institution=stmt.institution,
        asset_class=stmt.asset_class,
        flow_only=stmt.flow_only,
    )
    report.message = _cut_over(conn, acc_id, stmt, connector=imp.name == "connector")
    raw_rel = _archive(path, stmt, digest) if archive else str(path)
    period = stmt.period
    file_id = int(
        conn.execute(
            text(
                "INSERT INTO import_files(sha256, filename, importer, account_id, raw_path,"
                " period_start, period_end, n_rows) VALUES (:h, :fn, :imp, :a, :raw, :ps, :pe, :n)"
            ),
            {
                "h": digest,
                "fn": report.file,
                "imp": imp.name,
                "a": acc_id,
                "raw": raw_rel,
                "ps": period[0].isoformat() if period else None,
                "pe": period[1].isoformat() if period else None,
                "n": len(stmt.rows),
            },
        ).lastrowid  # type: ignore[arg-type]
    )
    if period:
        report.period = (period[0].isoformat(), period[1].isoformat())

    existing = {
        r.source_key
        for r in conn.execute(
            text(
                "SELECT source_key FROM postings WHERE account_id = :a AND source_key IS NOT NULL"
            ),
            {"a": acc_id},
        )
    }
    stated: list[int] = []
    edits = load_edits(conn, account)  # your fixes to lines of this account (see edits.py)
    for row, key in zip(stmt.rows, source_keys(stmt.rows), strict=True):
        if key in existing:
            report.duplicates += 1
            continue
        existing.add(key)
        edit = edits.get(key)
        if edit is not None and edit.deleted:
            continue  # you deleted this line
        if edit is not None:
            row = apply_to_row(row, edit)
        txn_id = post_line(conn, account, stmt.currency, row, key, file_id, edit)
        if row.category:
            stated.append(txn_id)
        report.txn_ids.append(txn_id)
        report.new += 1
    if stated:
        conn.execute(
            text(
                "UPDATE transactions SET category_source = 'seed'"
                " WHERE id IN (SELECT value FROM json_each(:ids))"
            ),
            {"ids": json.dumps(stated)},
        )

    if stmt.holder:  # the statement's own account holder: transfers to this name are you
        conn.execute(
            text("INSERT OR IGNORE INTO owner_names(name, source) VALUES (:n, 'statement')"),
            {"n": stmt.holder},
        )
    if stmt.assertions:
        if imp.name == "connector":
            # Each sync reports today's balance again: the latest report for a day replaces it.
            conn.execute(
                text(
                    "DELETE FROM balance_assertions WHERE account_id = :a"
                    " AND date IN (SELECT value FROM json_each(:d)) AND import_file_id IN"
                    " (SELECT id FROM import_files WHERE importer = 'connector')"
                ),
                {"a": acc_id, "d": json.dumps([d.isoformat() for d, _ in stmt.assertions])},
            )
        _insert_assertions(conn, acc_id, file_id, stmt, prec)
    if stmt.valuations:
        _insert_valuations(conn, account, stmt, prec)
    if stmt.holdings is not None or stmt.trades:
        from pf.ledger.portfolio import record

        record(conn, acc_id, file_id, stmt)  # positions and trades, beside the ledger

    opening = refresh_opening_balance(conn, account)
    if opening is not None:
        report.opening_balance = str(opening)

    from pf.ledger.pipeline import after_import  # categorisation + transfer matching (M3)

    after_import(conn, report.txn_ids)
    report.discrepancies = reconcile(conn, account)
    conn.execute(text("DELETE FROM pending_imports WHERE sha256=:h"), {"h": digest})
    conn.execute(text("UPDATE seen_files SET outcome='imported' WHERE sha256=:h"), {"h": digest})
    return report


_INSTITUTION_ALIASES = {
    "charles schwab": "schwab",
    "interactive brokers": "ibkr",
    "american express": "amex",
    "bank of america": "bofa",
    "fidelity investments": "fidelity",
    "jpmorgan chase": "chase",
}


def _canon(institution: str | None) -> str:
    name = " ".join((institution or "").lower().replace(".", " ").split())
    return _INSTITUTION_ALIASES.get(name, name)


def _same_account(conn: Connection, stmt: ParsedStatement) -> str | None:
    """The account statement files already created for what a connector now reports: same type
    and institution, and the same card / account digits (in its name or a claimed binding)."""
    if not stmt.importer.startswith("conn_"):
        return None
    inst = _canon(stmt.institution)
    rows = [
        r
        for r in conn.execute(
            text(
                """SELECT a.name, a.institution,
                          (SELECT GROUP_CONCAT(b.hint) FROM account_bindings b
                           WHERE b.account_name = a.name) AS hints
                   FROM accounts a WHERE a.type = :t"""
            ),
            {"t": stmt.account_type},
        )
        if _canon(r.institution) == inst
    ]
    hint = stmt.account_hint
    if hint:
        hit = [r for r in rows if r.name.endswith(hint) or hint in (r.hints or "").split(",")]
        if len(hit) == 1:
            return str(hit[0].name)
    if len(rows) != 1:
        return None
    only = rows[0]
    if hint is None:
        return str(only.name)
    # Digits known but not in the only account: take it only if that account has no digits of
    # its own and no connector feeds it yet (a file import named 'Assets:IBKR:Brokerage'); a
    # card '…-0004' is never another card '…-0002'.
    fed = conn.execute(
        text(
            "SELECT 1 FROM import_files f JOIN accounts a ON a.id = f.account_id"
            " WHERE a.name = :n AND f.importer = 'connector' LIMIT 1"
        ),
        {"n": only.name},
    ).first()
    return None if fed or re.search(r"\d{3,}$", only.name) else str(only.name)


def _cut_over(
    conn: Connection, acc_id: int, stmt: ParsedStatement, *, connector: bool
) -> str | None:
    """Statement files and an API connection overlap: files keep the days they cover, the
    connection adds only later days (and a file imported afterwards only earlier ones), so no
    line is booked twice whatever order they arrive in. Rebuild replays the same order."""
    if connector:
        # Provider IDs/descriptions differ, so line-level hashes cannot deduplicate two APIs.
        # The first source that actually supplied flows or NAV owns this account. Empty Flex
        # reports do not claim it. Keep the alternate raw file for rebuilds, but book none of it.
        source = stmt.importer.removeprefix("conn_")
        for previous in conn.execute(
            text(
                "SELECT raw_path FROM import_files WHERE account_id = :a"
                " AND importer = 'connector' AND period_start IS NOT NULL ORDER BY id"
            ),
            {"a": acc_id},
        ):
            old = json.loads((get_settings().raw_dir / previous.raw_path).read_text())
            owner = old.get("source")
            if owner and owner != source:
                stmt.rows = []
                stmt.valuations = []
                stmt.assertions = []
                return tr(
                    "同一账户已由 {source} 提供数据，已跳过 {other} 的重复数据。",
                    source=owner,
                    other=source,
                )
        edge = conn.execute(
            text(
                "SELECT MAX(period_end) FROM import_files"
                " WHERE account_id = :a AND importer <> 'connector'"
            ),
            {"a": acc_id},
        ).scalar_one()
        if edge:
            stmt.rows = [r for r in stmt.rows if r.date.isoformat() > edge]
    else:
        edge = conn.execute(
            text(
                "SELECT MIN(period_start) FROM import_files"
                " WHERE account_id = :a AND importer = 'connector'"
            ),
            {"a": acc_id},
        ).scalar_one()
        if edge:
            stmt.rows = [r for r in stmt.rows if r.date.isoformat() < edge]


def _import_archive(
    conn: Connection,
    path: Path,
    digest: str,
    report: ImportReport,
    *,
    account: str | None,
    password: str | None,
) -> ImportReport:
    """A zipped statement: park it until the password is known, then import the file inside."""
    if conn.execute(text("SELECT 1 FROM archive_imports WHERE sha256=:h"), {"h": digest}).first():
        report.status = "already_imported"
        return report
    found = statement_zip(path)
    if found is None:
        raise ImportError_(f"{path.name}: " + tr("压缩包里没有可识别的账单文件"))
    member, locked = found
    pending = conn.execute(
        text("SELECT filename FROM pending_imports WHERE sha256=:h"), {"h": digest}
    ).first()
    if pending:
        report.file = pending.filename
    provider = provider_for_member(member)
    if locked and not password:
        # Probe locally before any ledger writes. Failed saved codes leave the file pending.
        # An unrecognised name (a bank's attachment may carry no bank name) tries every saved code.
        providers = [provider] if provider != "other" else list(ArchivePasswords.providers())
        for candidate in (c for p in providers for c in ArchivePasswords.candidates(p)):
            with tempfile.TemporaryDirectory() as probe:
                try:
                    extract_statement(path, Path(probe), candidate)
                except ImportError_:
                    continue
            password = candidate
            break
    if locked and not password:
        institution = {
            "alipay": "Alipay",
            "wechat": "WeChat",
            "cmb": "China Merchants Bank",
            "ningbo": "Bank of Ningbo",
            "boc": "Bank of China",
        }.get(provider)
        dest = get_settings().data_dir / "pending" / f"{digest}.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(path, dest)
        conn.execute(
            text("""INSERT INTO pending_imports(sha256, filename, stored_path, reason, institution)
                VALUES (:h, :fn, :p, 'needs_password', :inst)
                ON CONFLICT(sha256) DO UPDATE SET reason='needs_password'"""),
            {"h": digest, "fn": report.file, "p": str(dest), "inst": institution},
        )
        report.status = "needs_password"
        report.pending_sha = digest
        report.message = tr("{f} 需要解压密码", f=member)
        return report
    with tempfile.TemporaryDirectory() as tmp:
        inner = extract_statement(path, Path(tmp), password)
        inner_report = import_file(conn, inner, account=account)
        inner_sha = sha256_file(inner)
    if inner_report.status in ("imported", "already_imported"):
        conn.execute(
            text("""INSERT OR IGNORE INTO archive_imports(sha256, filename, inner_sha256)
                VALUES (:h, :fn, :i)"""),
            {"h": digest, "fn": report.file, "i": inner_sha},
        )
        conn.execute(text("DELETE FROM pending_imports WHERE sha256=:h"), {"h": digest})
        conn.execute(
            text("UPDATE seen_files SET outcome='imported' WHERE sha256=:h"), {"h": digest}
        )
    elif inner_report.status in ("needs_account", "unknown_format"):
        # The archive is decrypted; any next decision belongs to the inner statement.
        conn.execute(text("DELETE FROM pending_imports WHERE sha256=:h"), {"h": digest})
    return inner_report


def suggested_account(stmt: ParsedStatement) -> str:
    return suggested_name(stmt.account_type, stmt.institution, stmt.asset_class, stmt.account_hint)


def suggested_name(
    account_type: str, institution: str | None, asset_class: str | None, hint: str | None
) -> str:
    kinds = {"brokerage": "Brokerage", "retirement": "Retirement", "crypto": "Crypto"}
    kind = "Card" if account_type == "Liabilities" else kinds.get(asset_class or "", "Checking")
    suffix = f"-{hint}" if hint else ""
    return f"{account_type}:{(institution or '').replace(':', '-') or 'Bank'}:{kind}{suffix}"


def _pend(
    conn: Connection, path: Path, digest: str, report: ImportReport, stmt: ParsedStatement | None
) -> ImportReport:
    report.status = "needs_account" if stmt else "unknown_format"
    report.pending_sha = digest
    report.preview = build_preview(path)
    report.suggested_account = suggested_account(stmt) if stmt else None
    dest = get_settings().data_dir / "pending" / f"{digest}{path.suffix.lower()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(path, dest)
    conn.execute(
        text("""INSERT INTO pending_imports
            (sha256, filename, stored_path, reason, importer, institution, account_type,
             asset_class, hint, preview)
            VALUES (:h, :fn, :p, :r, :i, :inst, :at, :ac, :hint, :preview)
            ON CONFLICT(sha256) DO UPDATE SET reason=excluded.reason, importer=excluded.importer,
                institution=excluded.institution, account_type=excluded.account_type,
                asset_class=excluded.asset_class, hint=excluded.hint, preview=excluded.preview"""),
        {
            "h": digest,
            "fn": report.file,
            "p": str(dest),
            "r": report.status,
            "i": stmt.importer if stmt else None,
            "inst": stmt.institution if stmt else None,
            "at": stmt.account_type if stmt else None,
            "ac": stmt.asset_class if stmt else None,
            "hint": stmt.account_hint if stmt else None,
            "preview": json.dumps(report.preview.model_dump(), ensure_ascii=False),
        },
    )
    return report


def _insert_assertions(
    conn: Connection, acc_id: int, file_id: int, stmt: ParsedStatement, prec: int
) -> None:
    conn.execute(
        text(
            "INSERT OR IGNORE INTO balance_assertions(account_id, date, amount, commodity,"
            " import_file_id) VALUES (:a, :d, :amt, :c, :f)"
        ),
        [
            {
                "a": acc_id,
                "d": d.isoformat(),
                "amt": to_minor(b, prec),
                "c": stmt.currency,
                "f": file_id,
            }
            for d, b in stmt.assertions
        ],
    )


def _insert_valuations(conn: Connection, account: str, stmt: ParsedStatement, prec: int) -> None:
    """Market-value marks from a positions / NAV report. A mark you typed yourself for the same
    day wins; the adjustment transactions are regenerated by after_import."""
    # Provider valuations can have fractional minor units. Keep the source file as received and
    # round the mark at the ledger boundary, using the account currency's precision.
    exact = not stmt.importer.startswith("conn_")
    conn.execute(
        text(
            "INSERT INTO valuations(account_name, date, value, commodity, source)"
            " VALUES (:a, :d, :v, :c, 'statement')"
            " ON CONFLICT(account_name, date, commodity) DO UPDATE SET value = excluded.value"
            " WHERE valuations.source = 'statement'"
        ),
        [
            {
                "a": account,
                "d": d.isoformat(),
                "v": to_minor(v, prec, exact=exact),
                "c": stmt.currency,
            }
            for d, v in stmt.valuations
        ],
    )


def refresh_opening_balance(conn: Connection, account: str) -> Decimal | None:
    """Derive the account's opening balance from its EARLIEST balance assertion, so that
    statements starting mid-history still reconcile. Recomputed on every import (order-free)."""
    acc = conn.execute(
        text("SELECT id, currency FROM accounts WHERE name = :n"), {"n": account}
    ).one()
    for (tid,) in conn.execute(
        text(
            "SELECT DISTINCT t.id FROM transactions t JOIN postings p ON p.txn_id = t.id"
            " WHERE t.kind = 'opening' AND p.account_id = :a"
        ),
        {"a": acc.id},
    ).all():
        delete_txn(conn, tid)

    first = conn.execute(
        text(
            "SELECT date, amount, commodity FROM balance_assertions WHERE account_id = :a"
            " ORDER BY date LIMIT 1"
        ),
        {"a": acc.id},
    ).first()
    if first is None:
        return None
    booked = conn.execute(
        text(
            "SELECT COALESCE(SUM(amount), 0) FROM v_postings WHERE account_id = :a"
            " AND commodity = :c AND date <= :d"
        ),
        {"a": acc.id, "c": first.commodity, "d": first.date},
    ).scalar_one()
    missing = int(first.amount) - int(booked)
    if missing == 0:
        return None
    earliest = conn.execute(
        text("SELECT MIN(date) FROM v_postings WHERE account_id = :a"), {"a": acc.id}
    ).scalar_one()
    day_before = dt.date.fromisoformat(earliest or first.date) - dt.timedelta(days=1)
    prec = Commodities(conn).precision(first.commodity, "currency")
    amount = from_minor(missing, prec)
    post(
        conn,
        NewTxn(
            date=day_before,
            description=f"Opening balance {account}",
            kind="opening",
            status="confirmed",
            weight_commodity=first.commodity,
            postings=[
                NewPosting(account, amount, first.commodity),
                NewPosting(OPENING_BALANCES, -amount, first.commodity),
            ],
        ),
    )
    return amount


def _archive(path: Path, stmt: ParsedStatement, digest: str) -> str:
    """Copy the original file into raw/<institution>/<YYYY-MM>/ (read-only, never modified)."""
    raw_dir = get_settings().raw_dir
    try:
        return str(path.resolve().relative_to(raw_dir.resolve()))
    except ValueError:
        pass
    month = (stmt.period[1] if stmt.period else dt.date.today()).strftime("%Y-%m")
    inst = stmt.institution.replace(" ", "_")
    dest = raw_dir / inst / month / path.name
    if dest.exists() and sha256_file(dest) != digest:
        dest = dest.with_name(f"{dest.stem}.{digest[:8]}{dest.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(path, dest)
        dest.chmod(0o444)
    return str(dest.relative_to(raw_dir))
