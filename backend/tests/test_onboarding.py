from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from pf.api.app import app
from pf.db import make_engine
from pf.ledger.importing import import_file
from tests.conftest import DEMO

HEADER = ["交易时间", "交易对方", "金额", "余额"]
ROWS = [["2026-09-01", "商店", "-10.00", "90.00"], ["2026-09-30", "工资", "20.00", "110.00"]]
SPEC = {
    "institution": "测试银行",
    "account_type": "Assets",
    "asset_class": "cash",
    "currency": "CNY",
    "columns": {"date": "交易时间", "description": "交易对方", "amount": "金额", "balance": "余额"},
    "date_format": "%Y-%m-%d",
    "sign": "as_is",
    "header": HEADER,
}


def statement(path: Path) -> Path:
    if path.suffix == ".xlsx":
        pd.DataFrame(ROWS, columns=HEADER).to_excel(path, index=False, startrow=2)
    else:
        path.write_text(
            "导出账单\n\n" + ",".join(HEADER) + "\n" + "\n".join(",".join(r) for r in ROWS),
            encoding="gb18030",
        )
    return path


def test_claim_remembers_and_drains_pending(conn: Connection, env: Path) -> None:
    from pf.ledger.onboarding import claim_pending

    files = []
    for month in ("01", "02", "03"):
        f = env / f"Chase9999_Activity_2026{month}.CSV"
        shutil.copy(DEMO / f"Chase0002_Activity_2026{month}.CSV", f)
        files.append(f)
    first = import_file(conn, files[0])
    second = import_file(conn, files[1])
    assert first.status == second.status == "needs_account"
    assert first.suggested_account == "Liabilities:Chase:Card-9999"
    assert first.preview and first.pending_sha
    claimed = claim_pending(conn, first.pending_sha, "Liabilities:Chase:NewCard")
    assert claimed.status == "imported" and claimed.new > 0
    assert conn.execute(text("SELECT COUNT(*) FROM pending_imports")).scalar() == 0
    assert conn.execute(text("SELECT COUNT(*) FROM import_files")).scalar() == 2
    assert import_file(conn, files[2]).account == "Liabilities:Chase:NewCard"


@pytest.mark.parametrize("ext", [".csv", ".xlsx", ".tsv"])
def test_teach_and_reuse(conn: Connection, env: Path, ext: str) -> None:
    from pf.ledger.onboarding import claim_pending, teach_profile

    f = statement(env / ("export" + ext))
    if ext == ".tsv":
        f.write_text("\t".join(HEADER) + "\n" + "\n".join("\t".join(r) for r in ROWS))
    pending = import_file(conn, f)
    assert pending.status == "unknown_format" and pending.pending_sha
    assert pending.preview and pending.preview.header == HEADER
    assert pending.preview.header_row_index == (0 if ext == ".tsv" else 2)
    assert pending.preview.guesses.date == "交易时间"
    assert pending.preview.guesses.amount == "金额"
    assert "%Y-%m-%d" in pending.preview.guesses.date_formats
    taught = teach_profile(conn, pending.pending_sha, SPEC)
    assert taught.status == "needs_account"
    result = claim_pending(conn, pending.pending_sha, "Assets:Test:Checking")
    assert result.new == 2 and result.discrepancies == []
    # A fresh file uses the persisted profile and account binding, including Excel detection.
    if ext == ".xlsx":
        pd.DataFrame([["2026-10-01", "商店", "-2", "108"]], columns=HEADER).to_excel(f, index=False)
    else:
        f.write_text(",".join(HEADER) + "\n2026-10-01,商店,-2,108\n")
    assert import_file(conn, f).status == "imported"


@pytest.mark.parametrize(
    "columns,date_format",
    [
        ({**SPEC["columns"], "date": "交易对方"}, "%Y-%m-%d"),
        ({**SPEC["columns"], "amount": "不存在"}, "%Y-%m-%d"),
        (SPEC["columns"], "%m/%d/%Y"),
    ],
)
def test_wrong_teaching_is_atomic(
    conn: Connection, env: Path, columns: dict, date_format: str
) -> None:
    from pf.ledger.onboarding import teach_profile

    pending = import_file(conn, statement(env / "bad.csv"))
    with pytest.raises(ValueError, match=r"日期|列|解析"):
        teach_profile(
            conn,
            pending.pending_sha or "",
            {**SPEC, "columns": columns, "date_format": date_format},
        )
    assert conn.execute(text("SELECT COUNT(*) FROM user_profiles")).scalar() == 0
    assert conn.execute(text("SELECT COUNT(*) FROM pending_imports")).scalar() == 1


def test_downloads_scan_is_readonly_and_idempotent(env: Path) -> None:
    from pf.ops.inbox import scan_watch_dirs

    downloads = env / "downloads"
    downloads.mkdir()
    shutil.copy(DEMO / "Chase0002_Activity_202601.CSV", downloads)
    statement(downloads / "unknown.csv")
    (downloads / "notes.txt").write_text("Remember to buy milk")
    (downloads / "random.csv").write_text("name,color,count\ncat,blue,7\n")
    (downloads / "partial.csv.crdownload").write_text("incomplete")
    old = statement(downloads / "old.csv")
    os.utime(old, (time.time() - 46 * 86400,) * 2)
    before = {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in downloads.iterdir()}
    result = scan_watch_dirs(wait_settle=False)
    assert {r.status for r in result.imported} == {"imported", "unknown_format"}
    assert {r.file for r in result.imported} == {"Chase0002_Activity_202601.CSV", "unknown.csv"}
    with make_engine().begin() as c:
        assert c.execute(text("SELECT COUNT(*) FROM seen_files")).scalar() == 4
        assert (
            c.execute(text("SELECT COUNT(*) FROM seen_files WHERE outcome='ignored'")).scalar() == 2
        )
    assert not scan_watch_dirs(wait_settle=False).imported
    assert before == {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in downloads.iterdir()}


def test_onboarding_api_and_balance(env: Path) -> None:
    with TestClient(app) as c:
        institutions = c.get("/api/onboarding/institutions").json()
        assert {"alipay", "wechat", "ningbo", "cmb"} <= {i["id"] for i in institutions}
        assert all(i["steps_verified"] is False for i in institutions)
        assert not next(i for i in institutions if i["id"] == "cmb")["supported"]
        assert c.get("/api/onboarding/state").json()["step"] == 1
        state = {"selected_institutions": ["chase", "alipay"], "completed": True, "step": 6}
        assert c.put("/api/onboarding/state", json=state).json() == state
        assert c.get("/api/onboarding/state").json() == state
        for step in (0, 7):
            assert c.put("/api/onboarding/state", json={**state, "step": step}).status_code == 422
        assert (
            c.put(
                "/api/onboarding/state", json={**state, "selected_institutions": ["bogus"]}
            ).status_code
            == 400
        )
        f = statement(env / "api.csv")
        pending = c.post("/api/import", files=[("files", (f.name, f.read_bytes()))]).json()[0]
        sha = pending["pending_sha"]
        listed = c.get("/api/onboarding/pending").json()
        assert listed[0]["sha256"] == sha and listed[0]["preview"]["header"] == HEADER
        assert (
            c.post(f"/api/onboarding/pending/{sha}/teach", json={"spec": SPEC}).json()["status"]
            == "needs_account"
        )
        assert (
            c.post(
                f"/api/onboarding/pending/{sha}/claim",
                json={"account_name": "Assets:Test:API", "remember": True},
            ).json()["new"]
            == 2
        )
        balance = c.post(
            "/api/accounts/balance",
            json={
                "account": "Assets:Test:API",
                "date": "2026-09-30",
                "amount": "110",
                "commodity": "CNY",
            },
        )
        assert balance.status_code == 200 and balance.json()["discrepancies"] == []
        assert c.get("/api/onboarding/pending").json() == []
        assert c.get("/api/onboarding/checklist", params={"month": "bad"}).status_code == 422
        assert c.post("/api/inbox/scan").status_code == 200
    with make_engine().begin() as conn:
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM balance_assertions WHERE import_file_id IS NULL")
            ).scalar()
            == 1
        )
        assert (
            json.loads(
                conn.execute(
                    text("SELECT value FROM app_settings WHERE key='onboarding'")
                ).scalar_one()
            )
            == state
        )


def test_dismiss_and_missing_pending(env: Path) -> None:
    with TestClient(app) as c:
        f = statement(env / "dismiss.csv")
        r = c.post("/api/import", files=[("files", (f.name, f.read_bytes()))]).json()[0]
        with make_engine().begin() as conn:
            stored = Path(
                conn.execute(text("SELECT stored_path FROM pending_imports")).scalar_one()
            )
        sha = r["pending_sha"]
        assert c.delete(f"/api/onboarding/pending/{sha}").json() == {"ok": True}
        assert not stored.exists() and f.exists()
        assert c.delete(f"/api/onboarding/pending/{sha}").status_code == 404
        assert (
            c.post(
                f"/api/onboarding/pending/{sha}/claim", json={"account_name": "Assets:Test"}
            ).status_code
            == 404
        )


def test_checklist_uses_month_coverage(conn: Connection, env: Path) -> None:
    from pf.ledger.onboarding import checklist, claim_pending

    f = env / "Chase9999_Activity.CSV"
    f.write_text(
        "Transaction Date,Post Date,Description,Category,Type,Amount\n09/05/2026,09/05/2026,Shop,Food,Sale,-10\n"
    )
    p = import_file(conn, f)
    claim_pending(conn, p.pending_sha or "", "Liabilities:Chase:Test")
    assert checklist(conn, "2026-09")[0].status == "stale"
    f.write_text(f.read_text() + "09/30/2026,09/30/2026,Shop,Food,Sale,-10\n")
    import_file(conn, f)
    row = checklist(conn, "2026-09")[0]
    assert row.status == "ok" and row.last_transaction_date == "2026-09-30" and row.export_url
    assert checklist(conn, "2026-10")[0].status == "missing"


def test_binding_priority_and_one_time_claim(conn: Connection, env: Path) -> None:
    from pf.importers.base import ParsedStatement
    from pf.ledger.accounts_config import load_specs, resolve_account
    from pf.ledger.onboarding import claim_pending

    stmt = ParsedStatement("chase_credit", "Chase", "USD", [], "Liabilities", account_hint="0002")
    conn.execute(
        text(
            "INSERT INTO account_bindings(importer,hint,account_name) VALUES ('chase_credit','','Liabilities:Default')"
        )
    )
    assert resolve_account(stmt, load_specs(), conn) == "Liabilities:Default"
    conn.execute(
        text(
            "INSERT INTO account_bindings(importer,hint,account_name) VALUES ('chase_credit','0002','Liabilities:Exact')"
        )
    )
    assert resolve_account(stmt, load_specs(), conn) == "Liabilities:Exact"
    conn.execute(text("DELETE FROM account_bindings"))
    f = env / "Chase8888_Activity.CSV"
    shutil.copy(DEMO / "Chase0002_Activity_202601.CSV", f)
    pending = import_file(conn, f)
    with pytest.raises(ValueError):
        claim_pending(conn, pending.pending_sha or "", "Assets:Wrong")
    assert conn.execute(text("SELECT COUNT(*) FROM pending_imports")).scalar() == 1
    assert conn.execute(text("SELECT COUNT(*) FROM import_files")).scalar() == 0
    assert (
        claim_pending(conn, pending.pending_sha or "", "Liabilities:Once", remember=False).new > 0
    )
    assert conn.execute(text("SELECT COUNT(*) FROM account_bindings")).scalar() == 0
    shutil.copy(DEMO / "Chase0002_Activity_202602.CSV", f)
    assert import_file(conn, f).status == "needs_account"


def test_learned_format_rejects_missing_or_bad_later_dates(conn: Connection, env: Path) -> None:
    from pf.ledger.onboarding import claim_pending, teach_profile

    f = statement(env / "dates.csv")
    sha = import_file(conn, f).pending_sha or ""
    teach_profile(conn, sha, SPEC)
    claim_pending(conn, sha, "Assets:Test:Dates")
    for date in ("garbage", ""):
        f.write_text(",".join(HEADER) + f"\n2026-10-01,Shop,-1,109\n{date},Shop,-2,107\n")
        with pytest.raises(ValueError, match="日期"):
            import_file(conn, f)
    assert conn.execute(text("SELECT COUNT(*) FROM import_files")).scalar() == 1


def test_statement_sniff_does_not_require_a_known_date_format(env: Path) -> None:
    from pf.ops.inbox import scan_watch_dirs

    downloads = env / "downloads"
    downloads.mkdir()
    (downloads / "unusual.csv").write_text("Date,Description,Amount\nSept 30 2026,Shop,-10\n")
    (downloads / "broken.xlsx").write_bytes(b"PK\x03\x04not a valid workbook")
    result = scan_watch_dirs(wait_settle=False)
    assert [(r.file, r.status) for r in result.imported] == [("unusual.csv", "unknown_format")]
    assert not result.failed


def test_debit_credit_preview_and_teaching(conn: Connection, env: Path) -> None:
    from pf.ledger.onboarding import claim_pending, teach_profile

    f = env / "split.tsv"
    f.write_text(
        "日期\t摘要\t支出\t收入\t余额\n2026/09/01\t商店\t10\t\t90\n2026/09/30\t工资\t\t20\t110\n"
    )
    r = import_file(conn, f)
    assert r.preview and r.preview.guesses.debit == "支出" and r.preview.guesses.credit == "收入"
    spec = {
        **SPEC,
        "header": ["日期", "摘要", "支出", "收入", "余额"],
        "date_format": "%Y/%m/%d",
        "columns": {
            "date": "日期",
            "description": "摘要",
            "debit": "支出",
            "credit": "收入",
            "balance": "余额",
        },
    }
    teach_profile(conn, r.pending_sha or "", spec)
    result = claim_pending(conn, r.pending_sha or "", "Assets:Test:Split")
    assert result.new == 2 and result.discrepancies == []


def test_teaching_rolls_back_with_outer_transaction(env: Path) -> None:
    from pf.ledger.onboarding import teach_profile

    f = statement(env / "rollback.csv")
    with make_engine().begin() as c:
        sha = import_file(c, f).pending_sha or ""
    with pytest.raises(RuntimeError), make_engine().begin() as c:
        teach_profile(c, sha, SPEC)
        raise RuntimeError("abort request")
    with make_engine().begin() as c:
        assert c.execute(text("SELECT COUNT(*) FROM user_profiles")).scalar() == 0
        assert c.execute(text("SELECT reason FROM pending_imports")).scalar() == "unknown_format"


def test_teaching_identity_survives_encoding_change(conn: Connection, env: Path) -> None:
    from pf.ledger.onboarding import teach_profile

    first = statement(env / "gb.csv")
    sha = import_file(conn, first).pending_sha or ""
    profile = teach_profile(conn, sha, SPEC).importer
    second = env / "utf.csv"
    second.write_text(first.read_text(encoding="gb18030") + "\n2026-10-01,商店,-1,109\n")
    other = import_file(conn, second)
    assert other.importer == profile
    # Re-teaching identical columns should update the same learned format, not make detection ambiguous.
    teach_profile(conn, other.pending_sha or "", SPEC)
    assert conn.execute(text("SELECT COUNT(*) FROM user_profiles")).scalar() == 1


def test_balance_registers_new_currency(env: Path) -> None:
    with TestClient(app) as c:
        result = c.post(
            "/api/accounts/balance",
            json={
                "account": "Assets:Test:CAD",
                "date": "2026-09-30",
                "amount": "10.25",
                "commodity": "CAD",
            },
        )
        assert result.status_code == 200 and result.json()["discrepancies"] == []
        invalid = c.post(
            "/api/accounts/balance",
            json={
                "account": "Assets:Test:CAD",
                "date": "2026-09-30",
                "amount": "NaN",
                "commodity": "CAD",
            },
        )
        assert invalid.status_code == 422


def test_scan_api_reports_outcomes_and_filters_limits(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pf.ops import inbox

    downloads = env / "downloads"
    downloads.mkdir()
    shutil.copy(DEMO / "amex_activity_202601.csv", downloads)
    statement(downloads / "unknown.xlsx")
    (downloads / "notes.txt").write_text("shopping list")
    with (downloads / "oversized.csv").open("wb") as stream:
        stream.truncate(inbox.MAX_BYTES)
    monkeypatch.setattr(inbox, "SETTLE_SECONDS", 0)
    with TestClient(app) as c:
        result = c.post("/api/inbox/scan").json()
        assert {r["file"]: r["status"] for r in result["outcomes"]} == {
            "amex_activity_202601.csv": "imported",
            "unknown.xlsx": "unknown_format",
            "notes.txt": "ignored",
        }
        assert len(list(downloads.iterdir())) == 4
        monkeypatch.setattr(
            inbox, "_settled", lambda _: pytest.fail("seen files must skip settling")
        )
        assert not c.post("/api/inbox/scan").json()["imported"]


def test_downloads_error_is_retried_on_next_scan(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient failure must not blacklist the file forever; final outcomes still stick."""
    import pf.ops.inbox as inbox

    downloads = env / "downloads"
    downloads.mkdir()
    shutil.copy(DEMO / "Chase0002_Activity_202601.CSV", downloads)
    real = inbox.import_file
    calls = {"n": 0}

    def flaky(conn, path, **kw):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("database is locked")
        return real(conn, path, **kw)

    monkeypatch.setattr(inbox, "import_file", flaky)
    first = inbox.scan_watch_dirs(wait_settle=False)
    assert [name for name, _ in first.failed] == ["Chase0002_Activity_202601.CSV"]
    second = inbox.scan_watch_dirs(wait_settle=False)
    assert [r.status for r in second.imported] == ["imported"]
    assert not inbox.scan_watch_dirs(wait_settle=False).imported  # now final


def test_quiet_connected_account_counts_as_covered(conn: Connection, env: Path) -> None:
    """A synced account with no new lines is up to date, not missing a statement."""
    from pf.ledger.onboarding import checklist, claim_pending, coverage_through

    f = env / "Chase9999_Activity.CSV"
    f.write_text(
        "Transaction Date,Post Date,Description,Category,Type,Amount\n09/05/2026,09/05/2026,Shop,Food,Sale,-10\n"
    )
    p = import_file(conn, f)
    claim_pending(conn, p.pending_sha or "", "Liabilities:Chase:Test")
    assert checklist(conn, "2026-10")[0].status == "missing"
    # The same account, now kept current by a connector that last synced on Nov 2.
    conn.execute(
        text("UPDATE import_files SET importer = 'connector', imported_at = '2026-11-02T08:00:00Z'")
    )
    assert checklist(conn, "2026-10")[0].status == "ok"
    assert coverage_through(conn, "Liabilities:Chase:Test") == "2026-11-02"
