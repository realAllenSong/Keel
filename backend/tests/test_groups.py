"""Uncategorised lines grouped by merchant or person; one decision files the whole group."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from pf.api.app import app
from pf.db import make_engine
from pf.ledger.dedup import normalize
from pf.ledger.groups import group_key
from pf.ledger.importing import import_file

HEADER = "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"


def test_group_keys_are_rule_patterns() -> None:
    cases = {
        "ZELLE PAYMENT FROM WEICI ZHAO CONF# Z3J9SRYSD": "zelle payment from weici zhao",
        "ZELLE PAYMENT FROM JANE DOE BACXNS32LY93": "zelle payment from jane doe",
        "DUNKIN #350912 Q35": "dunkin",
        "CURSOR, AI POWERED IDE": "cursor, ai powered",
        "AMAZON.COM*AB12CD": "amazon.com",
    }
    for desc, key in cases.items():
        assert group_key(desc) == key
        assert key in normalize(desc)  # usable as a 'contains' rule on the description


def test_one_decision_files_a_group_and_future_lines(env: Path) -> None:
    rows = [
        ("CREDIT", "10/01/2026", "ZELLE PAYMENT FROM WEICI ZHAO CONF# A1", "30.00"),
        ("CREDIT", "10/08/2026", "ZELLE PAYMENT FROM WEICI ZHAO CONF# B2", "25.00"),
        ("DEBIT", "10/09/2026", "ZELLE PAYMENT TO WEICI ZHAO CONF# C3", "-12.00"),
        ("DEBIT", "10/10/2026", "OBSCURE KIOSK 1234", "-4.00"),
    ]
    f = env / "Chase0001_Activity_20261031.CSV"
    f.write_text(HEADER + "".join(f'{d},{dt},"{desc}",{amt},ACH,,,\n' for d, dt, desc, amt in rows))
    with TestClient(app) as c:
        with make_engine().begin() as conn:
            import_file(conn, f, account="Assets:Chase:Checking-0001")
        groups = c.get("/api/review/groups").json()
        by = {(g["key"], g["direction"]): g for g in groups}
        assert by[("zelle payment from weici zhao", "in")]["count"] == 2
        assert (
            by[("zelle payment to weici zhao", "out")]["count"] == 1
        )  # money out is another question
        g = by[("zelle payment from weici zhao", "in")]
        assert g["total"] == "55.00"
        r = c.post(
            "/api/review/groups/apply",
            json={"txn_ids": g["txn_ids"], "category": "Income:Reimbursements", "remember": True},
        )
        assert r.status_code == 200 and r.json()["count"] == 2
        assert ("zelle payment from weici zhao", "in") not in {
            (x["key"], x["direction"]) for x in c.get("/api/review/groups").json()
        }
        # A later line from the same person is filed by the remembered rule.
        later = env / "Chase0001_Activity_20261101.CSV"
        later.write_text(
            HEADER + 'CREDIT,11/02/2026,"ZELLE PAYMENT FROM WEICI ZHAO CONF# D4",18.00,ACH,,,\n'
        )
        with make_engine().begin() as conn:
            import_file(conn, later, account="Assets:Chase:Checking-0001")
            cat = conn.execute(
                text(
                    "SELECT a.name FROM transactions t JOIN postings p ON p.txn_id = t.id"
                    " JOIN accounts a ON a.id = p.account_id WHERE t.description LIKE '%D4' AND a.type = 'Income'"
                )
            ).scalar_one()
        assert cat == "Income:Reimbursements"
