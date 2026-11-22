from __future__ import annotations

import csv
import datetime as dt
import random
import shutil
from decimal import Decimal as D
from pathlib import Path

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection, text

from pf.db import make_engine, migrate
from pf.ledger.importing import import_file
from pf.ledger.rebuild import rebuild
from pf.ledger.reconcile import reconcile
from tests.conftest import DEMO


def balance(conn: Connection, account: str) -> D:
    v = conn.execute(
        text("SELECT COALESCE(SUM(amount),0) FROM v_postings WHERE account = :a"), {"a": account}
    ).scalar_one()
    return D(v).scaleb(-2)


def snapshot(conn: Connection) -> list[tuple]:
    return sorted(
        conn.execute(text("SELECT date, description, account, amount, kind FROM v_postings")).all()
    )


def import_all(conn: Connection, files: list[Path]) -> list:
    return [import_file(conn, f) for f in files]


def test_demo_import_reconciles(conn: Connection) -> None:
    files = sorted(DEMO.glob("*"))
    reports = import_all(conn, files)
    assert all(r.status == "imported" for r in reports)
    assert reconcile(conn) == []
    assert conn.execute(text("SELECT COUNT(*) FROM v_unbalanced")).scalar() == 0
    # Checking ends where the bank's own running balance says it does.
    latest = next(csv.DictReader((DEMO / "Chase0001_Activity_202610.CSV").open()))
    assert balance(conn, "Assets:Chase:Checking") == D(latest["Balance"])
    # Opening balance was derived, not given.
    assert balance(conn, "Equity:Opening-Balances") == D("-8420.17")


def test_overlap_export_is_fully_deduplicated(conn: Connection) -> None:
    import_all(conn, sorted(DEMO.glob("Chase0001_Activity_2026??.CSV")))
    before = balance(conn, "Assets:Chase:Checking")
    r = import_file(conn, DEMO / "Chase0001_Activity_20260815_20260930.CSV")
    assert r.new == 0 and r.duplicates > 0
    assert balance(conn, "Assets:Chase:Checking") == before


def test_reimport_same_file_is_noop(conn: Connection) -> None:
    f = DEMO / "amex_activity_202601.csv"
    first = import_file(conn, f)
    again = import_file(conn, f)
    assert first.new > 0 and again.status == "already_imported"


def test_import_order_does_not_matter(env: Path) -> None:
    files = sorted(DEMO.glob("Chase0001_*"))
    snaps = []
    for i, order in enumerate([files, list(reversed(files))]):
        db = env / f"o{i}.db"
        eng = make_engine(db)
        migrate(eng)
        with eng.begin() as c:
            import_all(c, order)
            assert reconcile(c) == []
            snaps.append(snapshot(c))
    assert snaps[0] == snaps[1]


def test_rebuild_equals_incremental(conn: Connection) -> None:
    import_all(conn, sorted(DEMO.glob("*")))
    before = snapshot(conn)
    rebuild(conn)
    assert snapshot(conn) == before
    assert reconcile(conn) == []


def test_raw_archive_is_readonly_copy(conn: Connection, env: Path) -> None:
    import_file(conn, DEMO / "amex_activity_202601.csv")
    archived = list((env / "raw").rglob("amex_activity_202601.csv"))
    assert len(archived) == 1
    assert not archived[0].stat().st_mode & 0o222


def test_unknown_account_is_pending(conn: Connection, env: Path) -> None:
    f = env / "Chase9999_Activity.CSV"
    shutil.copy(DEMO / "Chase0002_Activity_202601.CSV", f)
    assert import_file(conn, f).status == "needs_account"
    r = import_file(conn, f, account="Liabilities:Chase:Sapphire")
    assert r.new > 0


# ---------------------------------------------------------------- property test
HEADER = ["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"]
row_st = st.tuples(
    st.dates(dt.date(2025, 1, 1), dt.date(2025, 12, 31)),
    st.sampled_from(["COFFEE", "RENT", "PAYROLL", "GROCERY", "ATM"]),
    st.integers(-50_000, 50_000).filter(lambda c: c != 0),
)


@settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    rows=st.lists(row_st, min_size=1, max_size=60),
    seed=st.integers(0, 10_000),
    opening=st.integers(0, 10**7),
)
# Regression: identical same-day rows in a newest-first file (closing balance order bug).
@example(
    rows=[(dt.date(2025, 1, 1), "COFFEE", 1), (dt.date(2025, 1, 1), "COFFEE", 1)], seed=0, opening=0
)
# Both orders satisfy the balance chain with different closings -> must not assert.
@example(
    rows=[(dt.date(2025, 1, 1), "ATM", 500), (dt.date(2025, 1, 1), "ATM", -500)],
    seed=0,
    opening=1000,
)
def test_property_overlapping_exports(tmp_path_factory, rows, seed, opening) -> None:  # type: ignore[no-untyped-def]
    """Random statement, cut into random OVERLAPPING exports, imported in random order.
    Invariants: no duplicate is booked, no false balance assertion is ever derived, and every
    assertion reconciles."""
    tmp = tmp_path_factory.mktemp("prop")
    rows = sorted(rows, key=lambda r: r[0])
    rng = random.Random(seed)
    # Running balances over the full statement.
    bal, full = opening, []
    for d, desc, cents in rows:
        bal += cents
        full.append((d, desc, cents, bal))
    # Cut into windows that overlap; windows only cut at date boundaries (as banks do).
    dates = sorted({r[0] for r in full})
    windows, i = [], 0
    while i < len(dates):
        j = min(len(dates), i + rng.randint(1, 5))
        start = max(0, i - rng.randint(0, 2))
        windows.append((dates[start], dates[j - 1]))
        i = j
    rng.shuffle(windows)
    files = []
    for k, (a, b) in enumerate(windows):
        p = tmp / f"Chase0001_Activity_{k:03d}.CSV"
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            for d, desc, cents, rb in reversed([r for r in full if a <= r[0] <= b]):
                w.writerow(
                    [
                        "X",
                        d.strftime("%m/%d/%Y"),
                        desc,
                        f"{D(cents).scaleb(-2):.2f}",
                        "T",
                        f"{D(rb).scaleb(-2):.2f}",
                        "",
                    ]
                )
        files.append(p)

    eng = make_engine(tmp / "p.db")
    migrate(eng)
    with eng.begin() as c:
        for f in files:
            import_file(c, f, account="Assets:Bank:Checking", archive=False)
        got = c.execute(
            text(
                "SELECT COALESCE(SUM(amount),0) FROM v_postings WHERE account='Assets:Bank:Checking'"
            )
        ).scalar_one()
        asserted = c.execute(text("SELECT date, amount FROM balance_assertions")).all()
        # 1) Never assert a false balance: each derived assertion equals the true end-of-day balance.
        true_eod = {d.isoformat(): rb for d, _, _, rb in full}  # last write per day wins = closing
        for a in asserted:
            assert a.amount == true_eod[a.date]
        # 2) With any assertion the opening is recovered exactly; without one it stays unknown (0).
        expected = full[-1][3] if asserted else sum(r[2] for r in full)
        assert got == expected
        assert reconcile(c) == []
        assert c.execute(text("SELECT COUNT(*) FROM v_unbalanced")).scalar() == 0
