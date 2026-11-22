"""Tags: stored per statement line, so they survive a rebuild; totals across categories."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from pf.ledger import tags
from pf.ledger.categorize import set_category
from pf.ledger.edits import add_manual, edit_txn
from pf.ledger.importing import import_file
from pf.ledger.rebuild import rebuild
from tests.conftest import DEMO


def _ids(conn: Connection, like: str) -> list[int]:
    return [r.id for r in conn.execute(text("SELECT id FROM transactions WHERE description LIKE :d ORDER BY date"), {"d": like})]  # fmt: skip


def test_tags_group_across_categories_and_survive_rebuild(conn: Connection) -> None:
    for f in sorted(DEMO.glob("*")):
        import_file(conn, f)
    some = conn.execute(
        text("SELECT t.id, t.description FROM transactions t JOIN postings p ON p.txn_id = t.id"
             " JOIN accounts a ON a.id = p.account_id WHERE a.type = 'Expenses' AND t.kind = 'normal'"
             " GROUP BY t.id ORDER BY t.date LIMIT 3")
    ).all()  # fmt: skip
    ids = [r.id for r in some]
    assert tags.add(conn, ids, "  #Japan   Trip, ") == 3  # cleaned on the way in
    assert tags.set_tags(conn, ids[0], ["Japan Trip", "work", "work"]) == ["Japan Trip", "work"]
    assert tags.tags_for(conn, ids) == {
        ids[0]: ["Japan Trip", "work"],
        ids[1]: ["Japan Trip"],
        ids[2]: ["Japan Trip"],
    }

    total = tags.summary(conn)
    trip = next(t for t in total if t.tag == "Japan Trip")
    spent = conn.execute(
        text("SELECT SUM(p.amount) FROM postings p JOIN accounts a ON a.id = p.account_id"
             " WHERE a.type = 'Expenses' AND p.txn_id IN (SELECT value FROM json_each(:i))"),
        {"i": str(ids)},
    ).scalar_one()  # fmt: skip
    assert (trip.count, Decimal(trip.spent)) == (3, Decimal(spent) / 100)

    # Recategorising a line keeps its tag; so does rebuilding the whole ledger from statements.
    set_category(conn, ids[1], "Expenses:Travel")
    descriptions = sorted(r.description for r in some)
    rebuild(conn)
    again = conn.execute(
        text(f"SELECT description FROM transactions WHERE id IN ({tags.TAGGED})"),
        {"tag": "Japan Trip"},
    )
    assert sorted(r.description for r in again) == descriptions

    tags.remove(
        conn,
        [
            r.id
            for r in conn.execute(
                text(f"SELECT id FROM transactions WHERE id IN ({tags.TAGGED})"),
                {"tag": "Japan Trip"},
            )
        ],
        "Japan Trip",
    )
    assert [t.tag for t in tags.summary(conn)] == ["work"]


def test_hand_entered_lines_keep_their_tags_when_edited(conn: Connection) -> None:
    from pf.ledger.core import ensure_account

    ensure_account(conn, "Assets:Cash:Wallet", currency="USD")
    tid = add_manual(conn, date=dt.date(2026, 11, 5), account="Assets:Cash:Wallet", amount=Decimal("-20"),
                     category="Expenses:Food:Dining", description="ramen")  # fmt: skip
    tags.add(conn, [tid], "japan-trip")
    new = edit_txn(conn, tid, amount=Decimal("-25"))
    assert tags.tags_for(conn, [new]) == {new: ["japan-trip"]}
    assert tags.summary(conn)[0].spent == "25.00"


def test_api_tag_filter_and_totals(env: Path) -> None:
    from pf.api.app import app
    from pf.db import make_engine

    with TestClient(app) as c:
        with make_engine().begin() as conn:
            for f in sorted(DEMO.glob("*"))[:2]:
                import_file(conn, f)
        page = c.get("/api/transactions", params={"limit": 3, "kind": "normal"}).json()["items"]
        ids = [t["id"] for t in page]
        assert c.post("/api/tags/apply", json={"ids": ids[:2], "tag": "move"}).json() == {
            "changed": 2
        }
        assert c.put(
            f"/api/transactions/{ids[2]}/tags", json={"tags": ["move", "gift"]}
        ).json() == ["gift", "move"]
        tagged = c.get("/api/transactions", params={"tag": "move"}).json()
        assert tagged["total"] == 3 and all("move" in t["tags"] for t in tagged["items"])
        assert {t["tag"]: t["count"] for t in c.get("/api/tags").json()} == {"move": 3, "gift": 1}
        c.post("/api/tags/apply", json={"ids": ids, "tag": "move", "remove": True})
        assert [t["tag"] for t in c.get("/api/tags").json()] == ["gift"]


def test_tag_rule_tags_what_is_there_and_what_comes_later(conn: Connection, tmp_path: Path) -> None:
    def card(name: str, lines: str) -> Path:
        f = tmp_path / name
        f.write_text(
            "# keel statement\n# institution: Testcard\n# kind: card\ndate,description,amount\n"
            + lines
        )
        return f

    import_file(
        conn, card("a.csv", "2026-10-02,OPENAI *CHATGPT,-20.00\n2026-10-03,COFFEE SHOP,-4.50\n")
    )
    assert tags.add_rule(conn, "OpenAI", "ai-tools") == 1  # the line already in the ledger
    import_file(
        conn, card("b.csv", "2026-11-02,OPENAI *CHATGPT,-20.00\n2026-11-03,ANTHROPIC,-21.60\n")
    )
    total = {t.tag: (t.count, t.spent) for t in tags.summary(conn)}
    assert total == {"ai-tools": (2, "40.00")}  # the new OpenAI line was tagged on import
    rule = tags.rules(conn)[0]
    assert (rule.pattern, rule.tag) == ("openai", "ai-tools")
    tags.delete_rule(conn, rule.id)
    import_file(conn, card("c.csv", "2026-12-02,OPENAI *CHATGPT,-20.00\n"))
    assert tags.summary(conn)[0].count == 2  # no longer tagging; earlier tags stay
