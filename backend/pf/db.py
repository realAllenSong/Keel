"""SQLite engine (WAL, foreign keys, busy timeout) and schema migration."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event

from pf.settings import REPO_ROOT, get_settings

_engines: dict[Path, Engine] = {}
_watchers: dict[Path, sqlite3.Connection] = {}
_watch_lock = threading.Lock()


def make_engine(db_path: Path | None = None) -> Engine:
    path = (db_path or get_settings().db_path).resolve()
    if path in _engines:
        return _engines[path]
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    _engines[path] = engine
    return engine


def ledger_revision(db_path: Path | None = None) -> str:
    """A token that changes whenever anything commits a change to the ledger: this process, the
    background sync agent, the `keel` command, the inbox watcher. The app polls it and reloads
    its screens, so nobody has to restart Keel to see a sync.

    SQLite's `PRAGMA data_version` does the work: on one long-lived connection that never
    writes, it moves each time another connection (in any process) commits a change. The pid
    makes tokens from a restarted engine differ too."""
    path = (db_path or get_settings().db_path).resolve()
    with _watch_lock:
        conn = _watchers.get(path)
        if conn is None:
            make_engine(path)  # creates the folder and the file's WAL setup
            conn = sqlite3.connect(str(path), check_same_thread=False)  # it never writes
            _watchers[path] = conn
        version = conn.execute("PRAGMA data_version").fetchone()[0]
    return f"{os.getpid()}-{version}"


def migrate(engine: Engine | None = None) -> None:
    from alembic import command
    from alembic.config import Config

    engine = engine or make_engine()
    cfg = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


@contextmanager
def transaction(engine: Engine | None = None) -> Iterator[Connection]:
    """One atomic unit of work. Everything inside commits together or not at all."""
    with (engine or make_engine()).begin() as conn:
        yield conn
