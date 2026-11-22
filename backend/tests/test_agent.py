"""keel CLI and MCP: the same core, small results, clear errors."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from pf.agent import cli, core


@pytest.fixture
def keel(env: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    core._client = None  # a fresh in-process API for this test's ledger
    monkeypatch.setattr(core, "_saved_plan", lambda: None)  # never the developer's own plan
    yield
    if core._client is not None:
        core._client.__exit__(None, None, None)
        core._client = None


def test_cli_reads_writes_and_reports_errors(
    keel: None, capsys: pytest.CaptureFixture[str]
) -> None:
    def run(*argv: str) -> dict:
        assert cli.main([*argv, "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    acct = "Assets:Test:Checking"
    run(
        "add",
        "--date",
        "2026-10-03",
        "--amount",
        "5000",
        "--category",
        "Income:Salary",
        "--account",
        acct,
        "--description",
        "pay",
    )
    lunch = run(
        "add",
        "--date",
        "2026-10-05",
        "--amount",
        "-20",
        "--category",
        "Expenses:Uncategorized",
        "--account",
        acct,
        "--description",
        "SWEETGREEN NYC",
    )
    s = run("summary", "--month", "2026-10")
    assert (s["ok"], s["income"], s["spending"], s["saved"]) == (True, 5000.0, 20.0, 4980.0)
    assert run("review")["summary"]["uncategorized"] == 0  # a hand-entered line is already yours
    done = run(
        "categorize", str(lunch["id"]), "--category", "Expenses:Food:Dining", "--rule", "sweetgreen"
    )
    assert done["categorized"] == 1
    lines = run("transactions", "--category", "Expenses:Food", "--month", "2026-10")
    assert [(t["what"], t["amount"], t["category"]) for t in lines["items"]] == [
        ("SWEETGREEN NYC", "-20.00", "Food › Dining")
    ]
    assert run("assets", "--date", "2026-10-31")["net_worth"] == "4980.00"
    assert run("status")["review"]["uncategorized"] == 0
    assert run("plan")["source"] == "ledger defaults"

    assert cli.main(["summary", "--month", "October"]) == 1
    err = json.loads(capsys.readouterr().err)
    assert (err["ok"], err["error"]) == (False, "bad_month")
    assert cli.main(["categorize", "1", "--category", "Groceries"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "bad_category"


def test_mcp_tools_are_few_and_answer_in_one_compact_line(keel: None) -> None:
    import asyncio

    from pf.agent.mcp_server import build

    mcp = build()
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {
        "keel_status",
        "keel_summary",
        "keel_transactions",
        "keel_categorize",
        "keel_plan",
    } <= names
    assert len(names) <= 20 and all(t.description and len(t.description) < 200 for t in tools)
    assert all(
        t.outputSchema is None for t in tools
    )  # no structuredContent: results are not sent twice
    result = asyncio.run(mcp.call_tool("keel_status", {}))
    content = result[0] if isinstance(result, tuple) else result
    text = content[0].text
    assert "\n" not in text and json.loads(text)["ok"] is True
