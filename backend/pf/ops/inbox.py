"""Import each file atomically; only the dedicated Inbox owns and moves source files."""

from __future__ import annotations

import datetime as dt
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock

from sqlalchemy import text

from pf.db import make_engine, migrate
from pf.i18n import tr
from pf.importers.archive import is_zip, statement_zip
from pf.importers.base import ImportError_
from pf.importers.preview import looks_like_statement
from pf.importers.registry import detect
from pf.ledger.importing import ImportReport, import_file, sha256_file
from pf.ops.notify import notify
from pf.settings import get_settings

IGNORE_PREFIX = (".", "~$")
SETTLE_SECONDS = 2.0
EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".qfx", ".ofx", ".zip"}
MAX_BYTES = 20 * 1024 * 1024
# The desktop watcher and the API share a process. Serialize the entire scan:
# Alembic's context proxy and the check/import/archive sequence are not reentrant.
_scan_lock = Lock()


@dataclass
class FileOutcome:
    file: str
    path: str
    status: str
    message: str | None = None


@dataclass
class InboxResult:
    imported: list[ImportReport] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    outcomes: list[FileOutcome] = field(default_factory=list)


def _candidates(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and not p.is_symlink() and not p.name.startswith(IGNORE_PREFIX)
    )


def _settled(p: Path) -> bool:
    try:
        before = p.stat()
        time.sleep(SETTLE_SECONDS)
        after = p.stat()
        return (
            before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and after.st_size > 0
        )
    except FileNotFoundError:
        return False


def _process(directory: Path, *, owned: bool, wait_settle: bool) -> InboxResult:
    res = InboxResult()
    for f in _candidates(directory):
        try:
            if not owned:
                stat = f.stat()
                if (
                    f.suffix.lower() not in EXTENSIONS
                    or not 0 < stat.st_size < MAX_BYTES
                    or stat.st_mtime < time.time() - 45 * 86400
                ):
                    continue
            digest = sha256_file(f)
            with make_engine().begin() as conn:
                seen = conn.execute(
                    text("SELECT outcome FROM seen_files WHERE sha256=:h"), {"h": digest}
                ).scalar_one_or_none()
                imported = conn.execute(
                    text("SELECT 1 FROM import_files WHERE sha256=:h"), {"h": digest}
                ).first()
                # An earlier *error* (DB busy, a since-fixed bug) is retried; every other outcome
                # (imported, pending, ignored, dismissed) is final for that exact file content.
                if (seen and seen != "error") or imported:
                    res.skipped.append(f.name)
                    res.outcomes.append(
                        FileOutcome(f.name, str(f), "already_imported" if imported else str(seen))
                    )
                    conn.execute(
                        text("""INSERT OR IGNORE INTO seen_files(sha256, path, outcome)
                        VALUES (:h, :p, :o)"""),
                        {"h": digest, "p": str(f), "o": "already_imported" if imported else seen},
                    )
                    # Pending Inbox files can safely go to .done: their private copy is durable.
                    if owned:
                        _move(f, directory / ".done" / dt.date.today().isoformat())
                    continue
                # Already-seen downloads should never pay the settle delay again.
                if wait_settle and (not _settled(f) or sha256_file(f) != digest):
                    continue
                try:
                    # Only zips holding exactly one statement file (支付宝 / 微信 exports).
                    recognized = (
                        statement_zip(f) is not None if is_zip(f) else bool(detect(f, conn))
                    )
                except ImportError_:
                    recognized = False
                if not recognized and (is_zip(f) or not looks_like_statement(f)):
                    if owned:
                        raise ImportError_(f"{f.name}: no importer recognises this file")
                    outcome = "ignored"
                    res.skipped.append(f.name)
                else:
                    r = import_file(conn, f)
                    res.imported.append(r)
                    outcome = r.status
                conn.execute(
                    text(
                        "INSERT INTO seen_files(sha256, path, outcome) VALUES (:h, :p, :o)"
                        " ON CONFLICT(sha256) DO UPDATE SET path = excluded.path,"
                        " outcome = excluded.outcome"  # a retried 'error' row gets its final outcome
                    ),
                    {"h": digest, "p": str(f), "o": outcome},
                )
            res.outcomes.append(FileOutcome(f.name, str(f), outcome))
            if owned:
                _move(f, directory / ".done" / dt.date.today().isoformat())
        except (ImportError_, ValueError, OSError) as exc:
            res.failed.append((f.name, str(exc)))
            res.outcomes.append(FileOutcome(f.name, str(f), "error", str(exc)))
            if f.exists():
                digest = sha256_file(f)
                with make_engine().begin() as conn:
                    conn.execute(
                        text("""INSERT INTO seen_files(sha256, path, outcome) VALUES (:h, :p, 'error')
                        ON CONFLICT(sha256) DO UPDATE SET outcome='error' """),
                        {"h": digest, "p": str(f)},
                    )
            if owned and f.exists():
                dest = _move(f, directory / "failed")
                dest.with_suffix(dest.suffix + ".txt").write_text(
                    f"{dt.datetime.now():%F %T}\n{exc}\n"
                )
    return res


def _move(source: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / source.name
    if dest.exists():
        dest = directory / f"{source.stem}.{sha256_file(source)[:8]}{source.suffix}"
    shutil.move(str(source), dest)
    return dest


def _notify(res: InboxResult) -> None:
    if res.imported or res.failed:
        pending = sum(r.status in ("needs_account", "unknown_format") for r in res.imported)
        issues = sum(len(r.discrepancies) for r in res.imported)
        msg = tr("{n} 个文件，新增 {k} 笔", n=len(res.imported), k=sum(r.new for r in res.imported))
        if pending:
            msg += tr("，{n} 个文件待认领或识别格式", n=pending)
        if issues:
            msg += tr("，{n} 处对账不符", n=issues)
        if res.failed:
            msg += tr("，{n} 个文件处理失败", n=len(res.failed))
        notify(tr("账单扫描完成"), msg)


def process_inbox(inbox: Path | None = None, *, wait_settle: bool = True) -> InboxResult:
    with _scan_lock:
        inbox = inbox or get_settings().inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        migrate()
        res = _process(inbox, owned=True, wait_settle=wait_settle)
        _notify(res)
        return res


def scan_watch_dirs(*, wait_settle: bool = True) -> InboxResult:
    with _scan_lock:
        settings = get_settings()
        migrate()
        res = InboxResult()
        for directory in settings.watch_dirs:
            owned = directory.resolve() == settings.inbox_dir.resolve()
            if owned:
                directory.mkdir(parents=True, exist_ok=True)
            part = _process(directory, owned=owned, wait_settle=wait_settle)
            res.imported.extend(part.imported)
            res.skipped.extend(part.skipped)
            res.failed.extend(part.failed)
            res.outcomes.extend(part.outcomes)
        _notify(res)
        return res


def watch(inbox: Path | None = None, *, stop: Event | None = None) -> None:
    """Periodic scans also discover watch directories created after the watcher starts."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    settings = get_settings()
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    directories = [inbox] if inbox else settings.watch_dirs
    dirty = {"flag": True}

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event) -> None:  # type: ignore[no-untyped-def]
            dirty["flag"] = True

    obs = Observer()
    scheduled: set[Path] = set()
    obs.start()
    last_full = 0.0
    try:
        while stop is None or not stop.is_set():
            for directory in directories:
                if directory.is_dir() and directory not in scheduled:
                    obs.schedule(Handler(), str(directory), recursive=False)
                    scheduled.add(directory)
                    dirty["flag"] = True
            if dirty["flag"] or time.time() - last_full > 600:
                dirty["flag"] = False
                last_full = time.time()
                if inbox:
                    process_inbox(inbox)
                else:
                    scan_watch_dirs()
            if stop is None:
                time.sleep(1.0)
            else:
                stop.wait(1.0)
    finally:
        obs.stop()
        obs.join()
