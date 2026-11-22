"""Export / import every user decision (rules, overrides, valuations, names that are you, fixes to
imported lines) as one YAML document. Manual transactions live in the ledger itself.

These are the only things `pf rebuild` cannot re-derive from raw/, so this file is also a
portable, human-readable backup of your bookkeeping judgement."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from pf.ledger.categorize import categorize
from pf.ledger.valuation import refresh_valuations
from pf.money import Commodities, from_minor, to_minor

RULE_COLS = (
    "pattern",
    "target_account",
    "match_type",
    "field",
    "payee",
    "account_filter",
    "amount_min",
    "amount_max",
    "priority",
)


def export_decisions(conn: Connection) -> dict[str, Any]:
    comms = Commodities(conn)
    rules = [
        {k: v for k, v in r._mapping.items() if v is not None}
        for r in conn.execute(
            text(
                f"SELECT {', '.join(RULE_COLS)} FROM rules WHERE source IN ('user','llm') ORDER BY id"
            )
        )
    ]
    overrides = [
        {k: v for k, v in r._mapping.items() if v is not None}
        for r in conn.execute(
            text(
                "SELECT account_name, source_key, target_account, payee FROM overrides ORDER BY 1, 2"
            )
        )
    ]
    valuations = [
        {
            "account": r.account_name,
            "date": r.date,
            "value": str(from_minor(r.value, comms.precision(r.commodity, "currency"))),
            "commodity": r.commodity,
        }
        for r in conn.execute(
            # statement marks come back from raw/ on rebuild; only yours are decisions
            text("SELECT * FROM valuations WHERE source = 'user' ORDER BY account_name, date")
        )
    ]
    names = [
        r.name
        for r in conn.execute(
            # names from statements' headers come back from raw/; only the ones you added are yours
            text("SELECT name FROM owner_names WHERE source = 'user' ORDER BY name")
        )
    ]
    line_edits = [
        {k: v for k, v in r._mapping.items() if v is not None and not (k == "deleted" and not v)}
        for r in conn.execute(text("SELECT * FROM line_edits ORDER BY account_name, source_key"))
    ]
    return {
        "version": 1,
        "line_edits": line_edits,
        "rules": rules,
        "overrides": overrides,
        "valuations": valuations,
        "owner_names": names,
    }


def import_decisions(conn: Connection, data: dict[str, Any]) -> dict[str, int]:
    """Merge decisions into the ledger (rules are appended unless an identical one exists)."""
    n_rules = 0
    for r in data.get("rules", []):
        row = {k: r.get(k) for k in RULE_COLS}
        row["match_type"] = row["match_type"] or "contains"
        row["field"] = row["field"] or "description"
        row["priority"] = row["priority"] or 100
        exists = conn.execute(
            text(
                "SELECT 1 FROM rules WHERE pattern = :pattern AND target_account = :target_account"
            ),
            row,
        ).first()
        if exists:
            continue
        conn.execute(
            text(
                f"INSERT INTO rules({', '.join(RULE_COLS)}, source) VALUES"
                f" ({', '.join(':' + c for c in RULE_COLS)}, 'user')"
            ),
            row,
        )
        n_rules += 1
    for o in data.get("overrides", []):
        conn.execute(
            text(
                "INSERT INTO overrides(account_name, source_key, target_account, payee)"
                " VALUES (:account_name, :source_key, :target_account, :payee)"
                " ON CONFLICT(account_name, source_key) DO UPDATE SET"
                " target_account = excluded.target_account, payee = excluded.payee"
            ),
            {"payee": None, **o},
        )
    comms = Commodities(conn)
    for v in data.get("valuations", []):
        c = v.get("commodity", "USD")
        conn.execute(
            text(
                "INSERT INTO valuations(account_name, date, value, commodity) VALUES (:a,:d,:v,:c)"
                " ON CONFLICT(account_name, date, commodity)"
                " DO UPDATE SET value = excluded.value, source = 'user'"
            ),
            {
                "a": v["account"],
                "d": dt.date.fromisoformat(str(v["date"])).isoformat(),
                "v": to_minor(Decimal(str(v["value"])), comms.precision(c, "currency")),
                "c": c,
            },
        )
        from pf.ledger.core import ensure_account

        ensure_account(conn, v["account"], currency=c)
    for e in data.get("line_edits", []):
        conn.execute(
            text(
                "INSERT OR REPLACE INTO line_edits(account_name, source_key, deleted, date, amount,"
                " description, payee, note, splits) VALUES (:account_name, :source_key, :deleted,"
                " :date, :amount, :description, :payee, :note, :splits)"
            ),
            {
                "deleted": 0,
                "date": None,
                "amount": None,
                "description": None,
                "payee": None,
                "note": None,
                "splits": None,
                **e,
            },
        )
    for name in data.get("owner_names", []):
        conn.execute(
            text("INSERT OR IGNORE INTO owner_names(name, source) VALUES (:n, 'user')"), {"n": name}
        )
    if data.get("owner_names"):
        from pf.ledger.ownflows import match_own_flows

        match_own_flows(conn)
    categorize(conn)
    refresh_valuations(conn)
    return {
        "rules": n_rules,
        "overrides": len(data.get("overrides", [])),
        "valuations": len(data.get("valuations", [])),
    }
