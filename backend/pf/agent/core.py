"""What an agent can ask Keel, as plain functions returning small dicts.

The CLI (`keel …`, pf/agent/cli.py) and the MCP server (`keel mcp`, pf/agent/mcp_server.py) are
two thin faces over these functions, and these functions are a thin face over the same API the
app uses, called in-process: no server, no port, and it works whether or not Keel.app is open
(SQLite in WAL mode takes both). Results are compact on purpose: an agent pays for every token.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import warnings
from pathlib import Path
from typing import Any

APP_ID = "com.allensong.keel"
_client: Any = None


class KeelError(Exception):
    """Something the caller can fix: `code` is stable, `fix` says what to do."""

    def __init__(self, code: str, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.fix = code, message, fix


def api() -> Any:
    """The app's own API, in-process (FastAPI's test client runs the lifespan: migrations)."""
    global _client
    if _client is None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from starlette.testclient import TestClient

            from pf.api.app import app

            _client = TestClient(
                app, headers={"Accept-Language": os.environ.get("KEEL_LANG", "zh")}
            )
            _client.__enter__()
    return _client


def _call(method: str, path: str, **kw: Any) -> Any:
    r = api().request(method, "/api" + path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        raise KeelError(
            f"http_{r.status_code}", str(detail)[:300], "check the arguments with --help"
        )
    return r.json()


def _month(month: str | None) -> str:
    if month:
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise KeelError("bad_month", f"month must be YYYY-MM, got {month!r}")
        return month
    return dt.date.today().strftime("%Y-%m")


def _short(account: str | None) -> str:
    return " › ".join((account or "").split(":")[1:]) or (account or "")


# ---------------------------------------------------------------- reading
def status() -> dict[str, Any]:
    """Is the ledger healthy and current: range, to-dos, review queue, balances, connections."""
    rng = _call("GET", "/reports/range")
    home = _call("GET", "/home")
    return {
        "ledger": {"first_month": rng.get("first"), "last_month": rng.get("last")},
        "todos": [t["title"] for t in home["todos"]],
        "review": _call("GET", "/review/summary"),
        "balance_mismatches": [
            {"account": _short(d["account"]), "date": d["date"], "diff": d["diff"]}
            for d in _call("GET", "/reconcile")
        ],
        # The bank's balance moved and its lines have not synced yet: nothing to do.
        "waiting_for_sync": [
            {"account": _short(d["account"]), "date": d["date"], "diff": d["diff"]}
            for d in _call("GET", "/reconcile", params={"waiting": True})
        ],
        "connections": [
            {
                "source": c["source"],
                "status": c["status"],
                "last_sync": c["last_sync"],
                "message": c["message"],
            }
            for c in _call("GET", "/connections")
            if c["connected"]
        ],
    }


def summary(
    month: str | None = None, year: int | None = None, currency: str = "USD"
) -> dict[str, Any]:
    """Income, spending and savings for a month (default: this month) or a whole year, with the
    largest spending categories."""
    if year:
        today = dt.date.today()
        end = f"{year}-12" if year < today.year else today.strftime("%Y-%m")
        t = _call(
            "GET",
            "/reports/cashflow",
            params={"start": f"{year}-01", "end": end, "depth": 1, "currency": currency},
        )
        inc = sum(float(x) for x in t["income_total"])
        exp = sum(float(x) for x in t["expenses_total"])
        return {
            "period": str(year),
            "currency": currency,
            "income": round(inc, 2),
            "spending": round(exp, 2),
            "saved": round(inc - exp, 2),
            "savings_rate": round((inc - exp) / inc, 3) if inc else None,
            "by_month": [
                {"month": m, "income": t["income_total"][i], "spending": t["expenses_total"][i]}
                for i, m in enumerate(t["months"])
            ],
            "missing_fx": t.get("missing_fx") or [],
        }
    m = _month(month)
    t = _call(
        "GET", "/reports/cashflow", params={"start": m, "end": m, "depth": 2, "currency": currency}
    )
    inc, exp = float(t["income_total"][0]), float(t["expenses_total"][0])
    cats = sorted(
        (
            (r["category"], float(r["total"]))
            for r in t["expenses"]
            if r["category"].count(":") == 1
        ),
        key=lambda x: -x[1],
    )
    return {
        "period": m,
        "currency": currency,
        "income": round(inc, 2),
        "spending": round(exp, 2),
        "saved": round(inc - exp, 2),
        "savings_rate": round((inc - exp) / inc, 3) if inc else None,
        "top_spending": [
            {"category": _short(c), "amount": round(a, 2)} for c, a in cats[:8] if a > 0
        ],
        "income_sources": [
            {"source": _short(r["category"]), "amount": r["total"]}
            for r in t["income"]
            if r["category"].count(":") == 1 and float(r["total"])
        ],
        "missing_fx": t.get("missing_fx") or [],
    }


def transactions(
    month: str | None = None,
    category: str | None = None,
    account: str | None = None,
    query: str | None = None,
    pending: bool = False,
    limit: int = 40,
    currency: str = "USD",
    tag: str | None = None,
) -> dict[str, Any]:
    """Ledger lines, newest first. Filter by month, category (prefix, e.g. Expenses:Food),
    account, text, tag, or only those waiting for a category."""
    params: dict[str, Any] = {"limit": max(1, min(limit, 500)), "currency": currency}
    for k, v in (
        ("month", month),
        ("category", category),
        ("account", account),
        ("q", query),
        ("tag", tag),
    ):
        if v:
            params[k] = v
    if pending:
        params["status"] = "pending"
    page = _call("GET", "/transactions", params=params)
    return {
        "total": page["total"],
        "shown": len(page["items"]),
        "items": [
            {
                "id": t["id"],
                "date": t["date"],
                "what": (t.get("payee") or t["description"])[:60],
                "amount": t["amount"],
                "currency": t["commodity"],
                "category": _short(t.get("counter_account")),
                "account": _short(t["account"]),
                **({"pending": True} if t["status"] == "pending" else {}),
                **({"tags": t["tags"]} if t.get("tags") else {}),
            }
            for t in page["items"]
        ],
    }


def assets(date: str | None = None, currency: str = "USD") -> dict[str, Any]:
    """Net worth and every account's balance (assets and debts) today or on a date."""
    b = _call(
        "GET",
        "/reports/balance-sheet",
        params={"date": date or dt.date.today().isoformat(), "currency": currency},
    )
    return {
        "date": b["date"],
        "currency": currency,
        "net_worth": b["net_worth"],
        "assets": b["assets"],
        "debts": b["liabilities"],
        "by_class": b["by_class"],
        "accounts": [
            {
                "account": _short(ln["account"]),
                "class": ln.get("asset_class") or "debt",
                "balance": ln["balance"],
                **(
                    {"native": ln["original_balances"]}
                    if list(ln.get("original_balances") or {}) not in ([], [currency])
                    else {}
                ),
            }
            for ln in b["lines"]
        ],
        "missing_fx": b.get("missing_fx") or [],
    }


def holdings(date: str | None = None, currency: str = "USD") -> dict[str, Any]:
    """What brokerage accounts hold (newest broker report, or the one on or before a date):
    quantity, price, value, cost, unrealised gain per position."""
    h = _call(
        "GET", "/portfolio", params={"currency": currency, **({"date": date} if date else {})}
    )
    return {
        "as_of": h["as_of"],
        "currency": currency,
        "value": h["value"],
        "invested": h["invested"],
        "cash": h["cash"],
        "cost": h["cost"],
        "unrealized_gain": h["gain"],
        "day_change": h["day_change"],
        "accounts": [
            {
                "account": _short(a["account"]),
                "as_of": a["as_of"],
                "value": a["value"],
                "positions": [
                    {
                        "symbol": p["name"],
                        "quantity": p["quantity"],
                        "price": p["price"],
                        "value": p["value"],
                        **({"cost": p["cost"], "gain": p["gain"]} if p["cost"] else {}),
                        **({"cash": True} if p["cash_equivalent"] else {}),
                    }
                    for p in a["positions"]
                ],
            }
            for a in h["accounts"]
        ],
        "missing_fx": h["missing_fx"],
    }


def _trade_row(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": t["date"],
        "account": _short(t["account"]),
        "action": t["action"],
        "symbol": t["name"],
        "quantity": t["quantity"],
        "price": t["price"],
        "amount": t["amount"],
    }


def trades(
    start: str | None = None,
    end: str | None = None,
    symbol: str | None = None,
    account: str | None = None,
    include_cash: bool = False,
    limit: int = 60,
) -> dict[str, Any]:
    """Buys, sells, reinvestments, splits and option events, newest first. Money-market sweeps
    are left out unless include_cash."""
    params = {
        k: v
        for k, v in {"start": start, "end": end, "symbol": symbol, "account": account}.items()
        if v
    }
    rows = _call(
        "GET", "/portfolio/trades", params={**params, "include_cash": include_cash, "limit": 5000}
    )
    return {
        "total": len(rows),
        "shown": min(limit, len(rows)),
        "items": [_trade_row(t) for t in rows[:limit]],
    }


def gains(year: int | None = None, currency: str = "USD") -> dict[str, Any]:
    """How the investing has gone: the overall gain and annual rate against the same money in
    QQQ and SPY, dividends, then realised gains (first in first out) by year and sale by sale;
    sales whose purchase is before the broker's history are listed as cost unknown."""
    r = _call("GET", "/portfolio/realized", params={"currency": currency})
    keep = lambda d: year is None or d.startswith(str(year))  # noqa: E731
    p = _call("GET", "/portfolio/performance", params={"currency": currency})
    return {
        "currency": currency,
        # Everything since the broker's first trade: money in, money out, what is left, and the
        # same money in an index on the same days. Rates are annualised and money-weighted.
        "overall": {
            "since": p["since"],
            "invested": p["invested"],
            "taken_out": p["returned"],
            "still_held": p["value"],
            "gain": p["gain"],
            "annual_rate": p["rate"],
            "same_money_in": {
                v["ticker"]: {"gain": v["gain"], "annual_rate": v["rate"]} for v in p["versus"]
            },
            "dividends_by_year": p["dividends"],
            **({"note": p["note"]} if p["note"] else {}),
        },
        "realized_by_year": r["by_year"],
        # An estimate (first in first out; simple wash-sale test): the broker's 1099-B decides.
        "tax_view_by_year": r["tax"],
        "sales": [
            {
                "date": c["date"],
                "symbol": c["name"],
                "quantity": c["quantity"],
                "cost": c["cost"],
                "proceeds": c["proceeds"],
                "gain": c["gain"],
                "term": "long" if c["long_term"] else "short",
                **({"option_legs": c["legs"]} if c["legs"] > 1 else {}),
                **({"wash_sale_disallowed": c["wash"]} if c["wash"] else {}),
            }
            for c in r["sales"]
            if keep(c["date"])
        ],
        "cost_unknown": [
            {
                "date": u["date"],
                "symbol": u["name"],
                "quantity": u["quantity"],
                "proceeds": u["proceeds"],
            }
            for u in r["unmatched"]
            if keep(u["date"])
        ],
    }


def brokerage_report(start: str, end: str, currency: str = "USD") -> dict[str, Any]:
    """Brokerage and exchange accounts, what they held at `end`, what was traded from `start`
    to `end`: the facts an employer's brokerage-account certification asks for. Full account
    numbers and opening dates are not in Keel; the broker states those."""
    s = _call(
        "GET", "/portfolio/statement", params={"start": start, "end": end, "currency": currency}
    )
    return {
        "from": s["from"],
        "to": s["to"],
        "accounts": [
            {
                "institution": a["institution"],
                "account": _short(a["account"]),
                "number_ending": a["number_ending"],
                "first_activity_in_keel": a["first_activity"],
                "value": a["value"],
                "currency": a["currency"],
            }
            for a in s["accounts"]
        ],
        "holdings_at_end": [
            {
                "account": _short(a["account"]),
                "as_of": a["as_of"],
                "symbol": p["name"],
                "quantity": p["quantity"],
                "value": p["value"],
            }
            for a in s["holdings"]["accounts"]
            for p in a["positions"]
        ],
        # Accounts with no broker report that early: the first report with later trades undone.
        "holdings_at_end_from_trades": [
            {"account": _short(h["account"]), "symbol": h["name"], "quantity": h["quantity"]}
            for h in s["holdings_from_trades"]
        ],
        "trades": [_trade_row(t) for t in s["trades"]],
        "money_market_movements_not_listed": s["cash_movements"],
    }


def _bill_row(b: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": b["key"],
        "name": b["name"],
        "amount": b["amount"],
        "currency": b["currency"],
        "account": _short(b["account"]),
        "category": _short(b["category"]),
        **({"new": True} if b["new"] else {}),
        "when": f"day {b['day']}"
        if b["cadence"] == "monthly"
        else f"month {b['month']} day {b['day']}",
        "last": b["last_date"],
        "next": b["next_date"],
        **({"overdue_days": b["overdue_days"]} if b["overdue_days"] else {}),
        **(
            {"price_was": b["price_change"]["before"], "price_now": b["price_change"]["now"]}
            if b["price_change"]
            else {}
        ),
    }


def bills(
    dismiss: str | None = None, restore: str | None = None, currency: str = "USD"
) -> dict[str, Any]:
    """Recurring bills, subscriptions and income found in the ledger: amount, day, last and next
    date, price changes, and what leaves every month in total. `dismiss` (a key from this list)
    says a series is not a bill; `restore` brings one back."""
    if dismiss:
        b = _call("POST", "/bills/dismiss", params={"currency": currency}, json={"key": dismiss})
    elif restore:
        b = _call("DELETE", "/bills/dismiss", params={"currency": currency, "key": restore})
    else:
        b = _call("GET", "/bills", params={"currency": currency})
    return {
        "currency": currency,
        "fixed_per_month": b["monthly_total"],
        "fixed_per_year": b["yearly_total"],
        "monthly": [_bill_row(x) for x in b["monthly"]],
        "annual": [_bill_row(x) for x in b["annual"]],
        "income": [_bill_row(x) for x in b["income"]],
        "dismissed": [
            {"key": x["key"], "name": x["name"], "amount": x["amount"]} for x in b["dismissed"]
        ],
    }


def tags(
    tag: str | None = None,
    txn_ids: list[int] | None = None,
    remove: bool = False,
    currency: str = "USD",
    rule: str | None = None,
    delete_rule: int | None = None,
) -> dict[str, Any]:
    """Tags group transactions across categories (a trip, a move). No arguments: every tag with
    its count and what was spent under it, and the tag rules. With txn_ids and a tag: put the
    tag on those transactions (remove=True takes it off). `rule` (a merchant word) with a tag:
    every line of that merchant gets the tag, now and on later imports; `delete_rule` takes a
    rule's id. List a tag's lines with transactions(tag=...)."""
    if delete_rule is not None:
        _call("DELETE", f"/tags/rules/{delete_rule}")
    if rule:
        if not tag:
            raise KeelError("missing_tag", "a tag is needed", "pass --tag NAME")
        _call("POST", "/tags/rules", json={"pattern": rule, "tag": tag})
    if txn_ids:
        if not tag:
            raise KeelError("missing_tag", "a tag is needed", "pass --tag NAME")
        _call("POST", "/tags/apply", json={"ids": txn_ids, "tag": tag, "remove": remove})
    return {
        "currency": currency,
        "tags": [
            {
                "tag": t["tag"],
                "transactions": t["count"],
                "spent": t["spent"],
                "income": t["income"],
                "from": t["first"],
                "to": t["last"],
            }
            for t in _call("GET", "/tags", params={"currency": currency})
        ],
        "rules": _call("GET", "/tags/rules"),
    }


def goals(
    add: str | None = None,
    target: str | None = None,
    by: str | None = None,
    accounts: list[str] | None = None,
    currency: str = "USD",
    delete: int | None = None,
) -> dict[str, Any]:
    """Savings goals: saved so far, monthly pace, when each is reached, what the date needs.
    `add` (a name) with `target` creates one; `accounts` are the accounts whose money counts
    (none = net worth); `by` is a date. `delete` (an id) removes one."""
    if delete is not None:
        rows = _call("DELETE", f"/goals/{delete}")
    elif add:
        if not target:
            raise KeelError("missing_target", "a goal needs a target amount", "pass --target 10000")
        rows = _call(
            "POST",
            "/goals",
            json={
                "name": add,
                "target": target,
                "currency": currency,
                "due": by,
                "accounts": accounts or [],
            },
        )
    else:
        rows = _call("GET", "/goals")
    return {
        "goals": [
            {
                "id": x["id"],
                "name": x["name"],
                "target": x["target"],
                "currency": x["currency"],
                "saved": x["saved"],
                "progress": round(x["progress"], 3),
                "pace_per_month": x["monthly_pace"],
                **(
                    {
                        "by": x["due"],
                        "needed_per_month": x["needed_per_month"],
                        "on_track": x["on_track"],
                    }
                    if x["due"]
                    else {}
                ),
                **({"reached": True} if x["reached"] else {"reached_at_this_pace": x["eta"]}),
                "counts": [_short(a) for a in x["accounts"]] or "net worth",
            }
            for x in rows
        ]
    }


def review() -> dict[str, Any]:
    """Lines waiting for a category, grouped by merchant or person, with the txn ids to pass to
    `categorize` and a hint where Keel has one."""
    groups = _call("GET", "/review/groups")
    return {
        "summary": _call("GET", "/review/summary"),
        "groups": [
            {
                "merchant": g["key"],
                "direction": g["direction"],
                "count": g["count"],
                "total": g["total"],
                "when": g["first"] if g["first"] == g["last"] else f"{g['first']}..{g['last']}",
                "txn_ids": g["txn_ids"],
                **({"hint": g["hint"]} if g.get("hint") else {}),
            }
            for g in groups
        ],
    }


def categories() -> dict[str, Any]:
    """The categories and accounts a line can be filed under."""
    return {"categories": _call("GET", "/categories")}


def plan() -> dict[str, Any]:
    """Where the saved plan (or, without one, the ledger's own defaults) leads: the lowest cash
    point in the next 24 months, net worth in 1, 3 and 5 years, and the lighthouses."""
    state = _saved_plan()
    saved = state is not None
    if state is None:
        d = _call("GET", "/sim/defaults")
        state = {"scenario": d["scenario"], "model": d["model"]}
    bridge = _call("POST", "/sim/bridge", json={"scenario": state["scenario"], "months": 24})
    sim = _call(
        "POST",
        "/sim/run",
        json={"scenario": state["scenario"], "model": state["model"], "paths": 4000, "seed": 7},
    )
    p50 = sim["percentiles"].get("p50") or []
    sc = state["scenario"]
    return {
        "source": "saved plan" if saved else "ledger defaults",
        "assumptions": {
            "investable": sc.get("initial"),
            "income_per_year": sc.get("annual_income"),
            "spending_per_year": sc.get("annual_spending"),
            "work_years": sc.get("work_years"),
            "stages": [p.get("label") for p in sc.get("phases") or []],
        },
        "cash_24_months": {
            "start": round(bridge["start"]),
            "lowest": round(bridge["low"]),
            "lowest_month": bridge["low_month"],
            "first_month_below_zero": bridge["short_month"],
        },
        "net_worth_median": {f"{y}y": round(p50[y]) for y in (1, 3, 5, 10) if len(p50) > y},
        "lighthouses": [
            {"target": round(t), "years_from_now": y and round(y, 1), "probability": round(p, 2)}
            for t, y, p in zip(
                sim["lighthouse_targets"],
                sim["lighthouse_median_year"],
                sim["lighthouse_prob"],
                strict=False,
            )
            if t and t != float("inf")
        ],
        "money_lasts_probability": round(sim["success"], 3),
    }


def _saved_plan() -> dict[str, Any] | None:
    """The plan edited in the app lives in its preferences (camelCase JSON)."""
    import plistlib

    out = subprocess.run(["defaults", "export", APP_ID, "-"], capture_output=True, check=False)
    if out.returncode or not out.stdout:
        return None
    try:
        raw = plistlib.loads(out.stdout).get("keel.plan.state.v1")
        state = json.loads(raw) if raw else None
    except Exception:
        return None
    if not state or state.get("cashflowReviewed") is False:
        return None

    def snake(o: Any) -> Any:
        if isinstance(o, dict):
            return {re.sub(r"(?<!^)([A-Z])", r"_\1", k).lower(): snake(v) for k, v in o.items()}
        return [snake(x) for x in o] if isinstance(o, list) else o

    s = snake(state)
    return (
        {"scenario": s["scenario"], "model": s["model"]}
        if "scenario" in s and "model" in s
        else None
    )


# ---------------------------------------------------------------- changing
def categorize(txn_ids: list[int], category: str, rule: str | None = None) -> dict[str, Any]:
    """File lines under a category. With `rule` (a merchant word such as "anthropic"), the same
    merchant is filed there from now on."""
    if not txn_ids:
        raise KeelError(
            "no_ids", "give at least one transaction id", "see `keel review` or `keel transactions`"
        )
    if category not in _call("GET", "/categories") and not category.startswith(
        ("Expenses:", "Income:", "Assets:", "Liabilities:")
    ):
        raise KeelError(
            "bad_category", f"unknown category {category!r}", "list them with `keel categories`"
        )
    also = 0
    for i, tid in enumerate(txn_ids):
        body: dict[str, Any] = {
            "category": category,
            "remember": "rule" if rule and i == 0 else "override",
        }
        if rule and i == 0:
            body["pattern"] = rule
        also += _call("POST", f"/transactions/{tid}/category", json=body).get("also_updated", 0)
    return {"categorized": len(txn_ids), "also_updated_by_rule": also, "category": category}


def add(date: str, amount: str, category: str, account: str, description: str) -> dict[str, Any]:
    """Record money that moved without a statement (cash, a friend paying back). `amount` has
    the account's sign: negative is money out."""
    t = _call(
        "POST",
        "/transactions",
        json={
            "date": date,
            "amount": amount,
            "category": category,
            "account": account,
            "description": description,
        },
    )
    return {
        "id": t["id"],
        "date": t["date"],
        "amount": t["amount"],
        "category": _short(t.get("counter_account")),
    }


def import_files(paths: list[str], account: str | None = None) -> dict[str, Any]:
    """Import statement files (CSV, Excel, zip). Files that need an account or a password are
    left waiting in the app. `account` files them under that ledger account.

    A statement no importer reads (a PDF, a screenshot): write its lines as a Keel statement
    file and import that. First line `# keel statement`, then optional `# institution: Chase`,
    `# kind: bank|card`, `# account: 1234`, `# currency: USD`, then CSV with the header
    date,description,amount[,balance] (YYYY-MM-DD; ledger sign: money into a bank account +,
    a card purchase -, a card's debt a negative balance). With `balance`, Keel checks its total
    against the statement's and reports any difference."""
    files = []
    for p in paths:
        f = Path(p).expanduser()
        if not f.is_file():
            raise KeelError("no_file", f"{p} is not a file")
        files.append(("files", (f.name, f.read_bytes(), "application/octet-stream")))
    res = _call(
        "POST", "/import", files=files, **({"data": {"account": account}} if account else {})
    )
    return {
        "files": [
            {
                "file": r["file"],
                "status": r["status"],
                "account": _short(r.get("account")),
                "new": r["new"],
                "duplicates": r["duplicates"],
                **({"message": r["message"]} if r.get("message") else {}),
                **(
                    {
                        "balance_differences": [
                            {"date": d["date"], "diff": d["diff"]} for d in r["discrepancies"]
                        ]
                    }
                    if r.get("discrepancies")
                    else {}
                ),
            }
            for r in res
        ]
    }


def sync(source: str | None = None) -> dict[str, Any]:
    """Pull new data from connected banks and brokers now (all of them, or one source)."""
    res = (
        _call("POST", f"/connections/{source}/sync")
        if source
        else _call("POST", "/connections/sync")
    )
    res = res if isinstance(res, list) else [res]
    return {
        "synced": [
            {
                "source": r["source"],
                "ok": r["ok"],
                "new": r["new"],
                **({"message": r["message"]} if r.get("message") else {}),
                **(
                    {
                        "balance_differences": [
                            {"date": d["date"], "diff": d["diff"]} for d in r["discrepancies"]
                        ]
                    }
                    if r.get("discrepancies")
                    else {}
                ),
            }
            for r in res
        ]
    }


def check_balance(
    account: str, amount: str, date: str | None = None, currency: str = "USD"
) -> dict[str, Any]:
    """Tell Keel what an account's balance really is (from the bank's app); Keel reconciles to it."""
    r = _call(
        "POST",
        "/accounts/balance",
        json={
            "account": account,
            "date": date or dt.date.today().isoformat(),
            "amount": amount,
            "commodity": currency,
        },
    )
    return {
        "account": _short(account),
        "matches": not r["discrepancies"],
        "mismatches": [
            {"date": d["date"], "bank": d["statement"], "keel": d["ledger"]}
            for d in r["discrepancies"]
        ],
    }


def close(month: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Whether a finished month can be closed (every account in, balances agree, nothing to
    review); with confirm=True, close it."""
    m = month or (dt.date.today().replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    s = (
        _call("POST", "/close", json={"month": m})
        if confirm
        else _call("GET", "/close", params={"month": m})
    )
    return {
        "month": m,
        "ready": s["ready"],
        "closed_at": s["closed_at"],
        "income": s["income"],
        "spending": s["expenses"],
        "open_checks": [c["detail"] or c["label"] for c in s["checks"] if not c["ok"]],
    }


def doctor() -> dict[str, Any]:
    """Is Keel installed and reachable: app, data folder, version."""
    from pf.settings import get_settings

    s = get_settings()
    v = _call("GET", "/app/version")
    return {
        "app_installed": Path("/Applications/Keel.app").exists(),
        "version": v.get("version"),
        "channel": v.get("channel"),
        "ledger": str(s.db_path),
        "ledger_exists": s.db_path.exists(),
        "app_running": subprocess.run(
            ["pgrep", "-f", "Keel.app/Contents/MacOS/Keel"], capture_output=True, check=False
        ).returncode
        == 0,
    }
