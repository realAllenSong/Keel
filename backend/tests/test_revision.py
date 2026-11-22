"""The app reloads when the ledger changes behind its back (background sync, `keel`, inbox)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from pf.api.app import app
from pf.db import make_engine
from pf.settings import get_settings


def test_revision_moves_on_writes_from_anywhere_and_never_on_reads(env: Path) -> None:
    with TestClient(app) as c:
        rev = lambda: c.get("/api/revision").json()["revision"]  # noqa: E731
        first = rev()
        assert rev() == first  # asking does not move it

        # Reading the app's screens must not move it either, or the app would reload forever.
        for path in (
            "/api/home",
            "/api/settings",
            "/api/transactions?limit=5",
            "/api/transactions?status=pending&limit=1",
            "/api/connections",
            "/api/assumptions",
            "/api/backup",
            "/api/close",
            "/api/version",
        ):
            assert c.get(path).status_code < 500, path
        assert rev() == first

        # A write through the API (this process).
        prefs = c.get("/api/settings").json()
        c.put("/api/settings", json={**prefs, "low_balance": "777"})
        second = rev()
        assert second != first

        # A write from this process outside a request (the daily job, the inbox watcher).
        with make_engine().begin() as conn:
            conn.execute(text("INSERT INTO app_settings(key, value) VALUES ('t1', '1')"))
        third = rev()
        assert third != second

        # A write from another connection entirely: what the background agent or `keel` is.
        other = sqlite3.connect(str(get_settings().db_path))
        other.execute("INSERT INTO app_settings(key, value) VALUES ('t2', '2')")
        other.commit()
        other.close()
        assert rev() != third


def test_no_screen_the_app_reads_writes_the_ledger(env: Path) -> None:
    """Every parameterless GET, on a ledger with real-looking data: none may commit a change."""
    from pf.ledger.importing import import_file
    from tests.conftest import DEMO

    with TestClient(app) as c:
        with make_engine().begin() as conn:
            for f in sorted(DEMO.glob("*")):
                import_file(conn, f)
        before = c.get("/api/revision").json()["revision"]
        moved = []
        for route in app.routes:
            path = getattr(route, "path", "")
            if (
                "GET" not in getattr(route, "methods", ())
                or "{" in path
                or not path.startswith("/api")
            ):
                continue
            c.get(path)
            now = c.get("/api/revision").json()["revision"]
            if now != before:
                moved.append(path)
                before = now
        assert moved == []
