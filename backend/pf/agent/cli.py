"""keel: Keel's ledger from the command line, for you and for coding agents.

    keel status                         is the ledger current? what needs doing?
    keel summary --month 2026-10        income, spending, saved, top categories
    keel summary --year 2026
    keel transactions --month 2026-11 --category Expenses:Food --limit 20
    keel transactions --query anthropic
    keel assets                         net worth and every account
    keel holdings                       stocks and funds held: value, cost, gain
    keel trades --from 2026-07-01 --symbol NVDA
    keel gains --year 2026              realised gains, closed lot by closed lot
    keel brokerage-report --from 2026-07-01 --to 2026-09-30
    keel bills                          subscriptions and fixed bills, what leaves every month
    keel goals --add "Emergency fund" --target 20000 --by 2027-06-30 --account Assets:Chase:Savings-1234
    keel tags 9589 9692 --tag japan-trip   then: keel tags, keel transactions --tag japan-trip
    keel review                         lines waiting for a category (with txn ids)
    keel categorize 7401 7402 --category Expenses:Food:Dining [--rule sweetgreen]
    keel categories
    keel add --date 2026-11-10 --amount -20 --category Expenses:Food:Dining \
             --account Assets:Chase:Checking-0102 --description "cash lunch"
    keel import ~/Downloads/Chase1234_Activity.CSV
    keel sync [plaid|simplefin|snaptrade|okx|mailbox|ibkr_flex]
    keel check-balance --account Assets:Chase:Checking-0102 --amount 1234.56
    keel plan                           cash low point, net worth in 1/3/5 years
    keel close [--month 2026-10] [--yes]
    keel doctor | keel open | keel mcp

Add --json to any command for one JSON object. Never interactive. Exit code 0 on success;
on failure 1 with {"ok": false, "error": code, "message": ..., "fix": ...} on stderr.
Amounts are in the account's currency unless --currency is given (USD or CNY).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

from pf.agent import core


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="keel", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--json", action="store_true", help="print one JSON object")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name: str, help_: str) -> argparse.ArgumentParser:
        c = sub.add_parser(name, help=help_, description=help_)
        c.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help="print one JSON object"
        )
        return c

    cmd("status", "ledger health: range, to-dos, review queue, balance mismatches, connections")
    c = cmd("summary", "income, spending and savings for a month (default: this month) or a year")
    c.add_argument("--month", help="YYYY-MM")
    c.add_argument("--year", type=int)
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    c = cmd("transactions", "ledger lines, newest first")
    c.add_argument("--month", help="YYYY-MM")
    c.add_argument("--category", help="prefix, e.g. Expenses:Food")
    c.add_argument("--account", help="e.g. Liabilities:Chase:Card-0003")
    c.add_argument("--query", help="text in the description or merchant")
    c.add_argument("--pending", action="store_true", help="only lines waiting for a category")
    c.add_argument("--tag", help="only lines carrying this tag")
    c.add_argument("--limit", type=int, default=40)
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    c = cmd("assets", "net worth and every account's balance")
    c.add_argument("--date", help="YYYY-MM-DD (default: today)")
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    c = cmd("holdings", "what brokerage accounts hold: quantity, price, value, cost, gain")
    c.add_argument("--date", help="YYYY-MM-DD (default: the newest broker report)")
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    c = cmd("trades", "buys, sells, splits and option events, newest first")
    c.add_argument("--from", dest="start", help="YYYY-MM-DD")
    c.add_argument("--to", dest="end", help="YYYY-MM-DD")
    c.add_argument("--symbol", help="a ticker; matches options on it too")
    c.add_argument("--account")
    c.add_argument("--cash", action="store_true", help="include money-market sweeps")
    c.add_argument("--limit", type=int, default=60)
    c = cmd("gains", "realised gains on closed lots, first in first out")
    c.add_argument("--year", type=int)
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    c = cmd("brokerage-report", "accounts, holdings and trades for a period (a certification)")
    c.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    c.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    c = cmd("bills", "recurring bills and subscriptions: amount, day, next date, price changes")
    c.add_argument("--dismiss", metavar="KEY", help="this series is not a bill")
    c.add_argument("--restore", metavar="KEY", help="bring a dismissed series back")
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    c = cmd("tags", "tags with what was spent under each; with ids and --tag, tag those lines")
    c.add_argument("txn_ids", type=int, nargs="*")
    c.add_argument("--tag", help="the tag to put on (or take off) the given transactions")
    c.add_argument("--remove", action="store_true")
    c.add_argument(
        "--rule",
        metavar="MERCHANT",
        help="with --tag: tag every line of this merchant, from now on too",
    )
    c.add_argument("--delete-rule", type=int, metavar="ID")
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    c = cmd("goals", "savings goals: saved, pace, when each is reached; --add creates one")
    c.add_argument("--add", metavar="NAME")
    c.add_argument("--target", help="amount, e.g. 20000")
    c.add_argument("--by", help="YYYY-MM-DD")
    c.add_argument(
        "--account",
        action="append",
        help="an account whose money counts (repeatable); none = net worth",
    )
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    c.add_argument("--delete", type=int, metavar="ID")
    cmd("review", "lines waiting for a category, grouped by merchant, with txn ids")
    cmd("categories", "categories and accounts a line can be filed under")
    c = cmd("categorize", "file transactions under a category")
    c.add_argument("txn_ids", type=int, nargs="+")
    c.add_argument("--category", required=True)
    c.add_argument("--rule", help="merchant word: file this merchant there from now on")
    c = cmd("add", "record money that moved without a statement")
    for name in ("date", "amount", "category", "account", "description"):
        c.add_argument(f"--{name}", required=True)
    c = cmd("import", "import statement files (CSV, Excel, zip)")
    c.add_argument("paths", nargs="+")
    c.add_argument("--account", help="file the lines under this ledger account")
    c = cmd("sync", "pull new data from connected banks and brokers")
    c.add_argument(
        "source",
        nargs="?",
        choices=["plaid", "simplefin", "snaptrade", "okx", "mailbox", "ibkr_flex"],
    )
    c = cmd("check-balance", "tell Keel an account's real balance; it reconciles to it")
    c.add_argument("--account", required=True)
    c.add_argument("--amount", required=True)
    c.add_argument("--date", help="YYYY-MM-DD (default: today)")
    c.add_argument("--currency", default="USD", choices=["USD", "CNY"])
    cmd("plan", "where the plan leads: cash low point, net worth in 1/3/5 years, lighthouses")
    c = cmd("close", "can last month be closed? --yes closes it")
    c.add_argument("--month", help="YYYY-MM (default: last month)")
    c.add_argument("--yes", action="store_true")
    cmd("doctor", "is Keel installed and reachable")
    cmd("open", "open Keel.app")
    cmd("mcp", "run the MCP server on stdio (for chat apps without a shell)")
    return p


def run(a: argparse.Namespace) -> dict[str, Any]:
    match a.cmd:
        case "status":
            return core.status()
        case "summary":
            return core.summary(a.month, a.year, a.currency)
        case "transactions":
            return core.transactions(
                a.month, a.category, a.account, a.query, a.pending, a.limit, a.currency, a.tag
            )
        case "assets":
            return core.assets(a.date, a.currency)
        case "holdings":
            return core.holdings(a.date, a.currency)
        case "trades":
            return core.trades(a.start, a.end, a.symbol, a.account, a.cash, a.limit)
        case "gains":
            return core.gains(a.year, a.currency)
        case "brokerage-report":
            return core.brokerage_report(a.start, a.end, a.currency)
        case "bills":
            return core.bills(a.dismiss, a.restore, a.currency)
        case "tags":
            return core.tags(a.tag, a.txn_ids, a.remove, a.currency, a.rule, a.delete_rule)
        case "goals":
            return core.goals(a.add, a.target, a.by, a.account, a.currency, a.delete)
        case "review":
            return core.review()
        case "categories":
            return core.categories()
        case "categorize":
            return core.categorize(a.txn_ids, a.category, a.rule)
        case "add":
            return core.add(a.date, a.amount, a.category, a.account, a.description)
        case "import":
            return core.import_files(a.paths, a.account)
        case "sync":
            return core.sync(a.source)
        case "check-balance":
            return core.check_balance(a.account, a.amount, a.date, a.currency)
        case "plan":
            return core.plan()
        case "close":
            return core.close(a.month, a.yes)
        case "doctor":
            return core.doctor()
        case "open":
            subprocess.run(["open", "-a", "Keel"], check=False)
            return {"opened": "Keel.app"}
    raise core.KeelError("unknown_command", a.cmd)


def _text(v: Any, indent: int = 0) -> str:
    """Readable text for a person: key: value, lists of dicts as aligned rows."""
    pad = "  " * indent
    if isinstance(v, dict):
        out = []
        for k, x in v.items():
            if isinstance(x, dict | list) and x:
                out.append(f"{pad}{k}:")
                out.append(_text(x, indent + 1))
            else:
                out.append(f"{pad}{k}: {'-' if x in (None, [], {}) else x}")
        return "\n".join(out)
    if isinstance(v, list):
        if v and all(isinstance(x, dict) for x in v):
            return "\n".join(pad + "  ".join(f"{x}" for x in row.values()) for row in v)
        return "\n".join(f"{pad}{x}" for x in v)
    return f"{pad}{v}"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    as_json = getattr(args, "json", False)
    if args.cmd == "mcp":
        from pf.agent.mcp_server import serve

        serve()
        return 0
    try:
        result = run(args)
    except core.KeelError as e:
        print(
            json.dumps(
                {"ok": False, "error": e.code, "message": e.message, "fix": e.fix},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as e:  # an unexpected failure still answers in one line
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "internal",
                    "message": str(e)[:300],
                    "fix": "run `keel doctor`",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False) if as_json else _text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
