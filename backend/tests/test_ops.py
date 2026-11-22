from __future__ import annotations

import datetime as dt
import json
import plistlib
import shutil
import sqlite3
import subprocess
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import Connection, text

from pf.db import make_engine
from pf.ledger.importing import import_file
from pf.market.providers.fred import parse_csv
from pf.market.update import store_fx, update_market
from pf.ops import agents
from pf.ops.backup import restore, run_backup, select_keep
from pf.ops.inbox import process_inbox
from pf.ops.monthly import snapshot_month, summary_line
from pf.reports.common import Fx
from tests.conftest import DEMO


# ---------------------------------------------------------------- backup retention
def test_retention_is_bounded_and_keeps_newest() -> None:
    now = dt.datetime(2026, 10, 21, 3, 0)
    stamps = [now - dt.timedelta(days=d, hours=h) for d in range(0, 800) for h in (0, 12)]
    keep = select_keep(stamps, now)
    assert max(stamps) in keep
    # ~7 daily + ~8 weekly + ~12 monthly + one per year, with overlaps
    assert 15 <= len(keep) <= 30
    years = {s.year for s in keep}
    assert years == {s.year for s in stamps}  # every year still represented


def test_retention_daily_window() -> None:
    now = dt.datetime(2026, 10, 21)
    stamps = [now - dt.timedelta(days=d) for d in range(10)]
    keep = select_keep(stamps, now)
    assert all(now - dt.timedelta(days=d) in keep for d in range(8))


# ---------------------------------------------------------------- backup run / restore
def test_backup_skips_unchanged_verifies_and_restores(env: Path) -> None:
    def imp(name: str) -> None:  # each import commits, as in real use
        with make_engine().begin() as c:
            import_file(c, DEMO / name)

    imp("amex_activity_202601.csv")
    dest = env / "backups"
    t0 = dt.datetime(2026, 10, 1, 3, 0)

    r1 = run_backup(dest, now=t0)
    assert r1.snapshot is not None and r1.snapshot.exists()
    assert r1.raw_copied == 1  # the archived statement was mirrored

    r2 = run_backup(dest, now=t0 + dt.timedelta(days=1))
    assert r2.snapshot is None  # nothing changed -> nothing written

    imp("amex_activity_202602.csv")
    r3 = run_backup(dest, now=t0 + dt.timedelta(days=2))
    assert r3.snapshot is not None and r3.raw_copied == 1

    out = restore(r3.snapshot, env / "restored.db")
    n = sqlite3.connect(out).execute("SELECT COUNT(*) FROM import_files").fetchone()[0]
    assert n == 2
    with pytest.raises(FileExistsError):
        restore(r3.snapshot, out)
    assert not list(dest.glob("*.partial"))


# ---------------------------------------------------------------- inbox
def test_inbox_imports_moves_and_reports_failures(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PF_NOTIFY", "0")
    inbox = env / "Inbox"
    inbox.mkdir()
    shutil.copy(DEMO / "amex_activity_202601.csv", inbox)
    shutil.copy(DEMO / "Chase0002_Activity_202601.CSV", inbox)
    (inbox / "junk.csv").write_text("a,b\n1,2\n")
    (inbox / ".DS_Store").write_text("x")

    r = process_inbox(inbox, wait_settle=False)
    assert {x.file for x in r.imported} == {
        "amex_activity_202601.csv",
        "Chase0002_Activity_202601.CSV",
    }
    assert [name for name, _ in r.failed] == ["junk.csv"]
    assert (
        (inbox / "failed" / "junk.csv.txt")
        .read_text()
        .strip()
        .endswith("no importer recognises this file")
    )
    assert not (inbox / "amex_activity_202601.csv").exists()
    assert list((inbox / ".done").rglob("amex_activity_202601.csv"))

    shutil.copy(DEMO / "amex_activity_202601.csv", inbox)  # dropped again by mistake
    again = process_inbox(inbox, wait_settle=False)
    assert again.skipped == ["amex_activity_202601.csv"] and not again.imported


# ---------------------------------------------------------------- market data
FRED_CSV = "observation_date,DEXCHUS\n2026-10-13,7.1000\n2026-10-14,.\n2026-10-15,7.2500\n"


def test_fred_parse_skips_missing() -> None:
    assert parse_csv(FRED_CSV) == [
        (dt.date(2026, 10, 13), D("7.1000")),
        (dt.date(2026, 10, 15), D("7.2500")),
    ]


def test_fx_rates_feed_currency_conversion(conn: Connection) -> None:
    store_fx(conn, "CNY", parse_csv(FRED_CSV))
    fx = Fx(conn)
    assert fx.to_base(710000, "CNY", "2026-10-14") == 100000  # ¥7,100 = $1,000 at 7.10
    assert fx.to_base(725000, "CNY", "2026-10-20") == 100000  # latest rate on or before
    assert fx.to_base(100, "JPY", "2026-10-20") == 0 and fx.missing == {"JPY"}


def test_market_update_survives_provider_failure(conn: Connection) -> None:
    def flaky(series: str, start):  # type: ignore[no-untyped-def]
        if series == "DTB3":
            raise ConnectionError("offline")
        return parse_csv(FRED_CSV.replace("DEXCHUS", series))

    r = update_market(conn, fetch=flaky)
    assert r.fx_rows == 2 and "CPIAUCSL" in r.macro
    assert any("DTB3" in e for e in r.errors)


# ---------------------------------------------------------------- monthly snapshot
def test_monthly_snapshot_is_stored_and_summarised(conn: Connection) -> None:
    for f in sorted(DEMO.glob("Chase0001_Activity_2026??.CSV")):
        import_file(conn, f)
    rep = snapshot_month(conn, "2026-09")
    stored = json.loads(
        conn.execute(
            text("SELECT report FROM monthly_snapshots WHERE month='2026-09'")
        ).scalar_one()
    )
    assert stored["income"] == rep["income"] == "8422.96"
    assert "收入 $8,423" in summary_line(rep)
    snapshot_month(conn, "2026-09")  # idempotent
    assert conn.execute(text("SELECT COUNT(*) FROM monthly_snapshots")).scalar_one() == 1


# ---------------------------------------------------------------- launchd plists (not installed)
def test_agent_plists_are_valid(env: Path) -> None:
    sp = agents.specs()
    assert set(sp) == {"api", "watch", "daily", "monthly"}
    api = plistlib.loads(agents.plist_for("api", sp["api"]))
    assert api["Label"] == "com.allensong.keel.api" and api["KeepAlive"] is True
    assert api["EnvironmentVariables"]["PF_DB"].endswith("ledger.db")
    daily = plistlib.loads(agents.plist_for("daily", sp["daily"]))
    assert daily["StartCalendarInterval"] == {"Hour": 7, "Minute": 30} and "KeepAlive" not in daily
    monthly = plistlib.loads(agents.plist_for("monthly", sp["monthly"]))
    assert monthly["StartCalendarInterval"]["Day"] == 1


def test_agent_install_replaces_selected_legacy_jobs_only(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = env / "LaunchAgents"
    directory.mkdir()
    old_api = directory / "com.personal-finance.api.plist"
    old_watch = directory / "com.personal-finance.watch.plist"
    old_api.write_bytes(b"old api")
    old_watch.write_bytes(b"old watcher")
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(agents, "AGENTS_DIR", directory)
    monkeypatch.setattr(agents, "_launchctl", launchctl)

    assert agents.install(["api"]) == ["api"]
    assert not old_api.exists()
    assert old_watch.read_bytes() == b"old watcher"
    current = plistlib.loads((directory / "com.allensong.keel.api.plist").read_bytes())
    assert current["Label"] == "com.allensong.keel.api"
    assert any(args[0] == "bootout" and args[-1] == str(old_api) for args in calls)


def test_uninstall_removes_both_agent_generations(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = env / "LaunchAgents"
    directory.mkdir()
    paths = [
        directory / f"{prefix}.api.plist"
        for prefix in ("com.personal-finance", "com.allensong.keel")
    ]
    for path in paths:
        path.write_bytes(b"existing job")
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(agents, "AGENTS_DIR", directory)
    monkeypatch.setattr(agents, "_launchctl", launchctl)

    assert agents.uninstall() == ["api"]
    assert not any(path.exists() for path in paths)
    assert {args[-1] for args in calls} == {str(path) for path in paths}


def test_status_recognizes_a_legacy_job(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
        legacy_api = args[-1].endswith("/com.personal-finance.api")
        return subprocess.CompletedProcess(args, 0 if legacy_api else 1, "state = running\n", "")

    monkeypatch.setattr(agents, "_launchctl", launchctl)
    assert agents.status()["api"] == "running"
    assert agents.status()["watch"] == "not installed"


def test_failed_agent_install_preserves_the_legacy_job(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = env / "LaunchAgents"
    directory.mkdir()
    legacy = directory / "com.personal-finance.api.plist"
    legacy.write_bytes(b"existing api")

    def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1 if args[0] == "bootstrap" else 0, "", "failed")

    monkeypatch.setattr(agents, "AGENTS_DIR", directory)
    monkeypatch.setattr(agents, "_launchctl", launchctl)
    with pytest.raises(RuntimeError, match="bootstrap api"):
        agents.install(["api"])
    assert legacy.read_bytes() == b"existing api"


def test_engine_still_usable_after_backup(conn: Connection, env: Path) -> None:
    run_backup(env / "b", now=dt.datetime(2026, 1, 1))
    with make_engine().begin() as c:
        assert c.execute(text("SELECT 1")).scalar() == 1


def test_backup_api_reports_and_runs(env: Path) -> None:
    from fastapi.testclient import TestClient

    from pf.api.app import app

    with TestClient(app) as c:
        before = c.get("/api/backup").json()
        assert before["count"] == 0 and before["last"] is None
        after = c.post("/api/backup").json()
        assert after["count"] == 1 and after["last"] and after["dir"] == before["dir"]
        prefs = c.get("/api/settings").json()
        assert prefs["backup_icloud"] is False
