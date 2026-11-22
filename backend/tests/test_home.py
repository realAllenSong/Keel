from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from pf.api.app import app
from pf.db import make_engine
from pf.ledger.decisions import import_decisions
from pf.ledger.importing import import_file
from tests.conftest import DEMO


def test_home_empty_ledger_is_first_run(env: Path) -> None:
    with TestClient(app) as c:
        h = c.get("/api/home").json()
    assert h["has_data"] is False and h["sentence"] is None and h["voyage"] is None
    assert [s["done"] for s in h["first_run"]] == [False, False, False]


def test_home_on_demo_ledger(env: Path) -> None:
    with make_engine().begin() as conn:
        for f in sorted(DEMO.glob("*")):
            import_file(conn, f)
        import_decisions(conn, yaml.safe_load((DEMO.parent / "decisions.yaml").read_text()))
    with TestClient(app) as c:
        h = c.get("/api/home").json()
    s = h["sentence"]
    assert s["month"] == "2026-09" and s["income"] == "8422.96" and s["net_worth"] == "94951.20"
    v = h["voyage"]
    assert [lh["id"] for lh in v["lighthouses"]] == [1, 2, 3]
    assert v["wake"][-1]["net_worth"] == s["net_worth"]
    assert len(h["todos"]) <= 3
    # Suggestions are accepted automatically now; a review to-do appears only for unknown lines.
    if review := next((t for t in h["todos"] if t["kind"] == "review"), None):
        assert review["href"].startswith("/details") and review["count"] > 0
    assert h["first_run"][0]["done"] is True


def test_home_groups_pending_statements_by_account(env: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A fresh install (no account config) sees '3 new accounts', not '73 files'."""
    monkeypatch.setenv("PF_CONFIG_DIR", str(env / "empty-config"))
    files = sorted(DEMO.glob("*"))
    with TestClient(app) as c:
        c.post(
            "/api/import", files=[("files", (f.name, f.read_bytes(), "text/csv")) for f in files]
        )
        todo = next(t for t in c.get("/api/home").json()["todos"] if t["kind"] == "files")
    assert todo["title"] == f"发现 3 个新账户（{len(files)} 份账单）"
    assert todo["count"] == len(files)
