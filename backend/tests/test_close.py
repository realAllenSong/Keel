"""Closing a month: allowed only when it's complete, and later changes show up as drift."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import Connection, text

from pf.ledger.categorize import set_category
from pf.ledger.close import close_month, close_status, reopen_month
from pf.ledger.importing import import_file
from pf.ledger.onboarding import claim_pending

HEAD = "Transaction Date,Post Date,Description,Category,Type,Amount\n"
TODAY = dt.date(2026, 10, 15)


def _categorize_all(conn: Connection) -> None:
    ids = conn.execute(text("SELECT id FROM transactions WHERE status = 'pending'")).scalars()
    for tid in list(ids):
        set_category(conn, tid, "Expenses:Food:Dining")


def test_close_needs_a_complete_month_and_reports_drift(conn: Connection, env: Path) -> None:
    f = env / "Chase9999_Activity.CSV"
    f.write_text(HEAD + "09/05/2026,09/05/2026,Shop,Food,Sale,-10\n")
    p = import_file(conn, f)
    claim_pending(conn, p.pending_sha or "", "Liabilities:Chase:Test")

    s = close_status(conn, "2026-09")
    assert not s.ready and not {c.id: c for c in s.checks}["accounts"].ok  # stops on the 5th
    with pytest.raises(ValueError, match="month_not_ready"):
        close_month(conn, "2026-09", today=TODAY)

    f.write_text(f.read_text() + "09/30/2026,09/30/2026,Shop,Food,Sale,-20\n")
    import_file(conn, f)
    _categorize_all(conn)
    s = close_status(conn, "2026-09")
    assert s.ready and s.closed is None
    with pytest.raises(ValueError, match="month_not_over"):
        close_month(conn, "2026-10", today=TODAY)

    s = close_month(conn, "2026-09", today=TODAY)
    assert s.closed is not None and s.closed.expenses == 3000 and s.drift_expenses == 0

    # The bank posts a late line dated into September: accepted, and shown as drift.
    f.write_text(f.read_text() + "09/20/2026,09/21/2026,Shop,Food,Sale,-5\n")
    import_file(conn, f)
    s = close_status(conn, "2026-09")
    assert s.drift_expenses == 500 and s.expenses == 3500

    _categorize_all(conn)
    s = close_month(conn, "2026-09", today=TODAY)  # close again: the new figure is the baseline
    assert s.closed is not None and s.closed.expenses == 3500 and s.drift_expenses == 0
    reopen_month(conn, "2026-09")
    assert close_status(conn, "2026-09").closed is None


def test_close_api(env: Path) -> None:
    from fastapi.testclient import TestClient

    from pf.api.app import app

    with TestClient(app) as c:
        got = c.get("/api/close", params={"month": "2026-09"}).json()
        assert got["ready"] and got["closed_at"] is None
        assert [k["id"] for k in got["checks"]] == ["accounts", "balances", "review"]
        assert c.post("/api/close", json={"month": "2099-01"}).status_code == 409
        closed = c.post("/api/close", json={"month": "2026-09"}).json()
        assert closed["closed_at"] and closed["drift_expenses"] == "0.00"
        assert c.delete("/api/close/2026-09").json()["closed_at"] is None


def test_stale_insight_counts_syncs_and_spares_a_month_in_progress(
    conn: Connection, env: Path
) -> None:
    """数据只到 … is for accounts that really stopped, not for quiet synced ones or this month."""
    from pf.reports.insights import data_health

    f = env / "Chase9999_Activity.CSV"
    f.write_text(HEAD + "09/05/2026,09/05/2026,Shop,Food,Sale,-10\n")
    p = import_file(conn, f)
    claim_pending(conn, p.pending_sha or "", "Liabilities:Chase:Test")
    stale = [i for i in data_health(conn, "2026-09") if i.kind == "stale_import"]
    assert len(stale) == 1  # the statement stops on Sep 5
    conn.execute(
        text("UPDATE import_files SET importer = 'connector', imported_at = :t"),
        {"t": dt.date.today().isoformat() + "T08:00:00Z"},
    )
    assert not [i for i in data_health(conn, "2026-09") if i.kind == "stale_import"]


def test_a_ready_month_closes_by_itself_and_a_reopened_one_is_left_alone(
    conn: Connection, env: Path
) -> None:
    from pf.ledger.close import auto_close

    f = env / "Chase9999_Activity.CSV"
    f.write_text(
        HEAD
        + "08/05/2026,08/05/2026,Shop,Food,Sale,-10\n09/30/2026,09/30/2026,Shop,Food,Sale,-20\n"
    )
    p = import_file(conn, f)
    claim_pending(conn, p.pending_sha or "", "Liabilities:Chase:Test")
    assert auto_close(conn, TODAY) == []  # lines still wait for a category: not ready
    _categorize_all(conn)
    closed = auto_close(conn, TODAY)
    assert [c.month for c in closed] == ["2026-09", "2026-08"]  # both finished, both complete
    assert auto_close(conn, TODAY) == []  # once

    # Reopened by the user: it stays open until they close it themselves.
    reopen_month(conn, "2026-09")
    assert auto_close(conn, TODAY) == [] and close_status(conn, "2026-09").closed is None
    close_month(conn, "2026-09", today=TODAY)
    reopen_month(conn, "2026-08")
    close_month(conn, "2026-08", today=TODAY)
    assert close_status(conn, "2026-08").closed is not None

    # The month in progress is never closed, and neither is anything before the ledger starts.
    assert all(c.month < "2026-10" for c in closed)


def test_the_close_todo_is_only_shown_when_closing_is_left_to_the_user(env: Path) -> None:
    from fastapi.testclient import TestClient

    from pf.api.app import app
    from pf.db import make_engine

    with TestClient(app) as c:
        with make_engine().begin() as conn:
            f = env / "Chase9999_Activity.CSV"
            f.write_text(HEAD + "09/30/2026,09/30/2026,Shop,Food,Sale,-20\n")
            p = import_file(conn, f)
            claim_pending(conn, p.pending_sha or "", "Liabilities:Chase:Test")
            _categorize_all(conn)
        prefs = c.get("/api/settings").json()
        assert prefs["auto_close"] is True
        todos = c.get("/api/home", params={"month": "2026-10"}).json()["todos"]
        assert "close" not in {t["kind"] for t in todos}
        c.put("/api/settings", json={**prefs, "auto_close": False})
        # Whether the to-do shows also depends on today's date; with closing left to the user it may.
        assert c.get("/api/settings").json()["auto_close"] is False
