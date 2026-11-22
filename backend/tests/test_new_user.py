"""What a brand-new user meets: a card statement with no balance, a payment to a card Keel
doesn't have yet, and money sent to an account they won't import."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pf.api.app import app

CARD = (
    "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
    "10/05/2026,10/06/2026,TRADER JOE S #555,Groceries,Sale,-55.39,\n"
)
CHECKING = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "DEBIT,10/12/2026,CHASE CREDIT CRD AUTOPAY PPD ID: 4760039224,-842.68,ACH_DEBIT,9157.32,\n"
    "DEBIT,10/10/2026,VANGUARD BUY INVESTMENT,-1500.00,ACH_DEBIT,10000.00,\n"
)


@pytest.fixture
def fresh(env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A first launch's config folder: no bindings yet (the engine writes exactly this)."""
    d = env / "config"
    d.mkdir()
    (d / "accounts.local.yaml").write_text("accounts: []\n")
    monkeypatch.setenv("PF_CONFIG_DIR", str(d))
    return env


def _import(c: TestClient, path: Path, body: str) -> None:
    path.write_text(body)
    r = c.post("/api/import", files=[("files", (path.name, path.read_bytes(), "text/csv"))]).json()[
        0
    ]
    if r["status"] == "needs_account":
        c.post(
            f"/api/onboarding/pending/{r['pending_sha']}/claim",
            json={"account_name": r["suggested_account"]},
        )


def test_card_without_balance_asks_to_be_checked(fresh: Path) -> None:
    with TestClient(app) as c:
        _import(c, fresh / "Chase0002_Activity_20261108.CSV", CARD)
        todos = [t for t in c.get("/api/home").json()["todos"] if t["kind"] == "balance"]
        assert [t["title"] for t in todos] == ["Chase 信用卡 0002 还没核对过余额"]
        steps = {s["id"]: s["done"] for s in c.get("/api/home").json()["first_run"]}
        assert steps["import"] and not steps["balance"]
        c.post(
            "/api/accounts/balance",
            json={
                "account": "Liabilities:Chase:Card-0002",
                "date": "2026-10-31",
                "amount": "-55.39",
            },
        )
        assert not [t for t in c.get("/api/home").json()["todos"] if t["kind"] == "balance"]


def test_review_hints_and_an_account_outside_keel(fresh: Path) -> None:
    with TestClient(app) as c:
        _import(c, fresh / "Chase0001_Activity_20261108.CSV", CHECKING)
        hints = {g["key"]: g["hint"] for g in c.get("/api/review/groups").json()}
        assert (
            "信用卡" in hints["chase credit crd"] and "投资账户" in hints["vanguard buy investment"]
        )
        assert "Assets:Elsewhere" in c.get("/api/categories").json()
