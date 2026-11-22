"""Persist onboarding choices so future exports need no configuration edits."""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Connection, NestedTransaction, RowMapping, text

from pf.i18n import LANG, tr
from pf.importers.generic_csv import GenericCsvImporter, Profile
from pf.importers.preview import build_preview, read_table
from pf.ledger.importing import ImportReport, import_file
from pf.onboarding_models import ChecklistItem, Institution, ProfileSpec


def institutions() -> list[Institution]:
    body = resources.files("pf.importers").joinpath("institutions.yaml").read_text(encoding="utf-8")
    out = []
    for item in yaml.safe_load(body):
        en = item.pop("en", None)
        if en and LANG.get() == "en":
            item.update(en)  # name / steps / note in English
        out.append(Institution.model_validate(item))
    return out


def pending_row(conn: Connection, sha: str) -> RowMapping:
    row = (
        conn.execute(text("SELECT * FROM pending_imports WHERE sha256=:h"), {"h": sha})
        .mappings()
        .first()
    )
    if row is None:
        raise KeyError(tr("找不到待处理账单"))
    return row


def _savepoint(conn: Connection) -> NestedTransaction:
    # SQLite's legacy driver does not BEGIN on SELECT. Without a real outer transaction,
    # releasing the first SAVEPOINT commits the entire claim even if the request rolls back.
    if conn.dialect.name == "sqlite" and not getattr(
        conn.connection.driver_connection, "in_transaction", True
    ):
        conn.exec_driver_sql("BEGIN")
    return conn.begin_nested()


def unlock_pending(
    conn: Connection, sha: str, password: str, remember: bool = False
) -> ImportReport:
    """Import a password-protected statement zip (支付宝 / 微信 send the code in-app)."""
    row = pending_row(conn, sha)
    if row["reason"] != "needs_password":
        raise ValueError(tr("这个文件不需要密码"))
    with _savepoint(conn):
        report = import_file(conn, Path(row["stored_path"]), password=password.strip())
    if remember:
        from pf.importers.archive import statement_zip
        from pf.importers.archive_passwords import ArchivePasswords, provider_for_member

        found = statement_zip(Path(row["stored_path"]))
        if found:
            ArchivePasswords.remember(provider_for_member(found[0]), password)
    report.file = row["filename"]
    return report


def retry_saved_archive_passwords(conn: Connection, provider: str) -> dict:
    from pf.importers.archive import statement_zip
    from pf.importers.archive_passwords import provider_for_member

    unlocked = new_transactions = errors = 0
    rows = conn.execute(
        text(
            "SELECT sha256, stored_path FROM pending_imports WHERE reason='needs_password' ORDER BY created_at, sha256"
        )
    ).all()
    for row in rows:
        try:
            found = statement_zip(Path(row.stored_path))
            if found is None or provider_for_member(found[0]) != provider:
                continue
            with _savepoint(conn):
                report = import_file(conn, Path(row.stored_path))
            if report.status != "needs_password":
                unlocked += 1
                new_transactions += report.new
        except (ValueError, OSError):
            errors += 1
    remaining = conn.execute(
        text("SELECT COUNT(*) FROM pending_imports WHERE reason='needs_password'")
    ).scalar_one()
    return dict(
        unlocked=unlocked, new_transactions=new_transactions, remaining=remaining, errors=errors
    )


def claim_pending(
    conn: Connection, sha: str, account_name: str, remember: bool = True
) -> ImportReport:
    row = pending_row(conn, sha)
    if not row["importer"]:
        raise ValueError(tr("请先识别账单格式，再认领账户"))
    # Savepoints also protect callers that catch validation failures inside a transaction.
    with _savepoint(conn):
        report = import_file(
            conn, Path(row["stored_path"]), account=account_name, importer=row["importer"]
        )
        if remember:
            conn.execute(
                text("""INSERT INTO account_bindings(importer, hint, account_name)
                VALUES (:i, :h, :a) ON CONFLICT(importer, hint) DO UPDATE SET account_name=excluded.account_name"""),
                {"i": row["importer"], "h": row["hint"] or "", "a": account_name},
            )
    if remember:
        others = conn.execute(
            text("""SELECT sha256, stored_path, importer FROM pending_imports
            WHERE importer=:i AND COALESCE(hint,'')=:h ORDER BY created_at, sha256"""),
            {"i": row["importer"], "h": row["hint"] or ""},
        ).all()
        for other in others:
            try:
                with _savepoint(conn):
                    import_file(conn, Path(other.stored_path), importer=other.importer)
            except (OSError, ValueError) as exc:
                report.message = tr("账户已认领；部分其他账单仍待处理：{e}", e=exc)
    return report


def teach_profile(conn: Connection, sha: str, spec: ProfileSpec | dict[str, Any]) -> ImportReport:
    row = pending_row(conn, sha)
    spec = ProfileSpec.model_validate(spec)
    path = Path(row["stored_path"])
    preview = build_preview(path)
    table, encoding = read_table(path)
    if preview.header_row_index is None:
        raise ValueError(tr("无法解析账单表头，请检查文件"))
    mapped = list(spec.columns.model_dump(exclude_none=True).values())
    if not all(c in preview.header for c in [*mapped, *spec.header]):
        raise ValueError(tr("所选列不存在，请检查列映射和检测表头"))
    # Every transaction date must parse; silently skipping a bad mapping would lose money.
    date_index = preview.header.index(spec.columns.date)
    amount_columns = (
        [spec.columns.amount] if spec.columns.amount else [spec.columns.debit, spec.columns.credit]
    )
    amount_indices = [preview.header.index(c) for c in amount_columns if c]
    for record in table[preview.header_row_index + 1 :]:
        if not any(record):
            continue
        if any(j < len(record) and record[j] for j in amount_indices):
            value = record[date_index] if date_index < len(record) else ""
            try:
                dt.datetime.strptime(value, spec.date_format)
            except ValueError as exc:
                raise ValueError(tr("日期无法解析：{v}，请检查日期列和格式", v=value)) from exc
    body = spec.model_dump(exclude={"header"})
    body["columns"] = spec.columns.model_dump(exclude_none=True)
    body.update(detect={"header": spec.header}, encoding=encoding, strict_dates=True)
    if path.suffix.lower() in (".xlsx", ".xls"):
        body["format"] = "xlsx"
    # Encoding is auto-detected for every file and must not fork account bindings.
    identity = {k: v for k, v in body.items() if k != "encoding"}
    pid = "user_" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    body["id"] = pid
    importer = GenericCsvImporter(Profile(body))
    try:
        parsed = importer.parse(path)
    except (ValueError, KeyError) as exc:
        raise ValueError(tr("无法解析账单，请检查列映射：{e}", e=exc)) from exc
    if not parsed.rows:
        raise ValueError(tr("没有可解析的交易行"))
    with _savepoint(conn):
        conn.execute(
            text(
                "INSERT INTO user_profiles(id, body) VALUES (:i, :b) ON CONFLICT(id) DO UPDATE SET body=excluded.body"
            ),
            {"i": pid, "b": json.dumps(body, ensure_ascii=False)},
        )
        return import_file(conn, path, importer=pid)


def dismiss_pending(conn: Connection, sha: str) -> None:
    row = pending_row(conn, sha)
    conn.execute(
        text("""INSERT INTO seen_files(sha256, path, outcome) VALUES (:h, :p, 'dismissed')
        ON CONFLICT(sha256) DO UPDATE SET outcome='dismissed', seen_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')"""),
        {"h": sha, "p": row["filename"]},
    )
    conn.execute(text("DELETE FROM pending_imports WHERE sha256=:h"), {"h": sha})
    Path(row["stored_path"]).unlink(missing_ok=True)


def _synced_or_checked(conn: Connection, account: str) -> str:
    """The day this account was last known complete without a statement: a connector sync
    returns everything posted up to the moment it ran (quiet accounts included), and a balance
    you entered by hand says the account was checked that day."""
    row = conn.execute(
        text(
            """SELECT MAX(d) FROM (
                 SELECT substr(f.imported_at, 1, 10) AS d FROM import_files f
                   JOIN accounts a ON a.id = f.account_id
                  WHERE a.name = :a AND f.importer = 'connector'
                 UNION ALL
                 SELECT date FROM v_postings WHERE account = :a AND kind = 'adjustment')"""
        ),
        {"a": account},
    ).scalar_one()
    return row or ""


def coverage_through(conn: Connection, account: str) -> str | None:
    """Last day this account's data is known complete through (statement, sync, or check)."""
    row = conn.execute(
        text(
            """SELECT MAX(d) FROM (
                 SELECT f.period_end AS d FROM import_files f JOIN accounts a ON a.id = f.account_id
                  WHERE a.name = :a AND f.importer != 'connector'
                 UNION ALL
                 SELECT MAX(date) FROM v_postings WHERE account = :a AND kind NOT IN ('opening', 'adjustment'))"""
        ),
        {"a": account},
    ).scalar_one()
    best = max(row or "", _synced_or_checked(conn, account))
    return best or None


def checklist(conn: Connection, month: str) -> list[ChecklistItem]:
    first = dt.date.fromisoformat(month + "-01")
    end = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    cutoff = end - dt.timedelta(days=9)
    catalog = institutions()
    names = (
        conn.execute(
            text("""SELECT account_name FROM account_bindings
        UNION SELECT a.name FROM accounts a JOIN import_files f ON f.account_id=a.id""")
        )
        .scalars()
        .all()
    )
    items: list[ChecklistItem] = []
    for name in names:
        account = conn.execute(
            text("SELECT id, institution FROM accounts WHERE name=:a"), {"a": name}
        ).first()
        inst = account.institution if account else None
        last = conn.execute(
            text("SELECT MAX(date) FROM v_postings WHERE account=:a AND kind != 'opening'"),
            {"a": name},
        ).scalar_one()
        period = conn.execute(
            text("""SELECT MAX(period_end) FROM import_files WHERE account_id=:a"""),
            {"a": account.id if account else None},
        ).scalar_one()
        coverage = conn.execute(
            text("""SELECT MAX(period_end) FROM import_files WHERE account_id=:a
            AND period_start<=:end AND period_end>=:start"""),
            {
                "a": account.id if account else None,
                "end": end.isoformat(),
                "start": first.isoformat(),
            },
        ).scalar_one()
        monthly_last = conn.execute(
            text(
                "SELECT MAX(date) FROM v_postings WHERE account=:a AND kind != 'opening' AND date BETWEEN :start AND :end"
            ),
            {"a": name, "start": first.isoformat(), "end": end.isoformat()},
        ).scalar_one()
        covered = max(coverage or "", monthly_last or "", _synced_or_checked(conn, name))
        status = "missing" if not covered else ("ok" if covered >= cutoff.isoformat() else "stale")
        entry = next(
            (i for i in catalog if inst and (i.name == inst or inst.lower() == i.id)), None
        )
        items.append(
            ChecklistItem(
                account=name,
                institution=inst,
                last_transaction_date=last,
                period_end=period,
                status=status,
                export_url=(entry.export_url or None) if entry else None,
                steps=entry.steps if entry else [],
            )
        )
    return items
