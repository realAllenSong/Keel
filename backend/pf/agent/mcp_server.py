"""keel mcp: Keel for chat apps that have no shell (Claude Desktop). stdio, same core as the CLI.

Results are one compact JSON line (structured_output off: FastMCP would otherwise send every
result twice)."""

from __future__ import annotations

import json
from typing import Any

from pf.agent import core


def _out(fn: Any, *args: Any, **kw: Any) -> str:
    try:
        return json.dumps(
            {"ok": True, **fn(*args, **kw)}, ensure_ascii=False, separators=(",", ":")
        )
    except core.KeelError as e:
        return json.dumps(
            {"ok": False, "error": e.code, "message": e.message, "fix": e.fix}, ensure_ascii=False
        )
    except Exception as e:
        return json.dumps(
            {"ok": False, "error": "internal", "message": str(e)[:300]}, ensure_ascii=False
        )


def build() -> Any:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "keel",
        instructions=(
            "Keel is the user's private personal-finance ledger on this Mac (banks, cards, Alipay, "
            "WeChat; USD and CNY). Read with keel_status, keel_summary, keel_transactions, keel_assets, "
            "keel_plan, keel_holdings, keel_trades, keel_gains, keel_brokerage_report, keel_bills, keel_tags, keel_goals. Change only what the user asks: keel_categorize, keel_add_transaction, "
            "keel_import, keel_sync, keel_check_balance, keel_close_month. Amounts are strings in the "
            "account's currency; money out is negative."
        ),
    )
    tool = lambda: mcp.tool(structured_output=False)  # noqa: E731

    @tool()
    def keel_status() -> str:
        """Is the ledger current: range, to-dos, lines to review, balance mismatches, connections."""
        return _out(core.status)

    @tool()
    def keel_summary(
        month: str | None = None, year: int | None = None, currency: str = "USD"
    ) -> str:
        """Income, spending, savings and top categories for a month (YYYY-MM, default this month) or a year."""
        return _out(core.summary, month, year, currency)

    @tool()
    def keel_transactions(
        month: str | None = None,
        category: str | None = None,
        account: str | None = None,
        query: str | None = None,
        pending: bool = False,
        limit: int = 40,
        tag: str | None = None,
    ) -> str:
        """Ledger lines, newest first. category is a prefix such as Expenses:Food; query matches merchant text; tag filters by tag."""
        return _out(core.transactions, month, category, account, query, pending, limit, "USD", tag)

    @tool()
    def keel_assets(date: str | None = None, currency: str = "USD") -> str:
        """Net worth and every account's balance, today or on a date (YYYY-MM-DD)."""
        return _out(core.assets, date, currency)

    @tool()
    def keel_holdings(date: str | None = None, currency: str = "USD") -> str:
        """Stocks, funds and options held in brokerage accounts: quantity, price, value, cost, unrealised gain."""
        return _out(core.holdings, date, currency)

    @tool()
    def keel_trades(
        start: str | None = None,
        end: str | None = None,
        symbol: str | None = None,
        account: str | None = None,
        include_cash: bool = False,
        limit: int = 60,
    ) -> str:
        """Buys, sells, reinvestments, splits and option events, newest first (dates YYYY-MM-DD)."""
        return _out(core.trades, start, end, symbol, account, include_cash, limit)

    @tool()
    def keel_gains(year: int | None = None, currency: str = "USD") -> str:
        """Realised gains on closed lots (first in first out) by year; sales with unknown cost listed apart."""
        return _out(core.gains, year, currency)

    @tool()
    def keel_brokerage_report(start: str, end: str, currency: str = "USD") -> str:
        """Brokerage accounts, holdings at `end`, trades from `start` to `end`: for an account certification."""
        return _out(core.brokerage_report, start, end, currency)

    @tool()
    def keel_bills(
        dismiss: str | None = None, restore: str | None = None, currency: str = "USD"
    ) -> str:
        """Recurring bills and subscriptions: amount, day, next date, price changes, fixed total per month. dismiss/restore take a key from the list."""
        return _out(core.bills, dismiss, restore, currency)

    @tool()
    def keel_tags(
        tag: str | None = None,
        txn_ids: list[int] | None = None,
        remove: bool = False,
        rule: str | None = None,
        delete_rule: int | None = None,
    ) -> str:
        """Tags group lines across categories. No arguments: tags, totals, rules. txn_ids+tag: tag those lines. rule (merchant word)+tag: tag that merchant now and on later imports."""
        return _out(core.tags, tag, txn_ids, remove, "USD", rule, delete_rule)

    @tool()
    def keel_goals(
        add: str | None = None,
        target: str | None = None,
        by: str | None = None,
        accounts: list[str] | None = None,
        currency: str = "USD",
        delete: int | None = None,
    ) -> str:
        """Savings goals: saved, monthly pace, when reached. add (name) + target creates one; accounts count toward it (none = net worth); delete takes an id."""
        return _out(core.goals, add, target, by, accounts, currency, delete)

    @tool()
    def keel_review() -> str:
        """Lines waiting for a category, grouped by merchant, with txn ids for keel_categorize."""
        return _out(core.review)

    @tool()
    def keel_categories() -> str:
        """Categories and accounts a line can be filed under."""
        return _out(core.categories)

    @tool()
    def keel_categorize(txn_ids: list[int], category: str, rule: str | None = None) -> str:
        """File transactions under a category; rule (a merchant word) files that merchant there from now on."""
        return _out(core.categorize, txn_ids, category, rule)

    @tool()
    def keel_add_transaction(
        date: str, amount: str, category: str, account: str, description: str
    ) -> str:
        """Record money that moved without a statement. amount has the account's sign: negative is money out."""
        return _out(core.add, date, amount, category, account, description)

    @tool()
    def keel_import(paths: list[str], account: str | None = None) -> str:
        """Import statement files (CSV, Excel, zip) by absolute path. For a PDF or screenshot, first write a Keel statement file: '# keel statement' line, then CSV date,description,amount[,balance]."""
        return _out(core.import_files, paths, account)

    @tool()
    def keel_sync(source: str | None = None) -> str:
        """Pull new data from connected banks and brokers (all, or plaid/simplefin/snaptrade/okx/mailbox/ibkr_flex)."""
        return _out(core.sync, source)

    @tool()
    def keel_check_balance(
        account: str, amount: str, date: str | None = None, currency: str = "USD"
    ) -> str:
        """Tell Keel an account's real balance from the bank; Keel reconciles to it."""
        return _out(core.check_balance, account, amount, date, currency)

    @tool()
    def keel_plan() -> str:
        """Where the plan leads: lowest cash in 24 months, net worth in 1/3/5/10 years, lighthouses."""
        return _out(core.plan)

    @tool()
    def keel_close_month(month: str | None = None, confirm: bool = False) -> str:
        """Can a finished month (default last month) be closed; confirm=true closes it."""
        return _out(core.close, month, confirm)

    return mcp


def serve() -> None:
    build().run()  # stdio
