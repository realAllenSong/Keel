"""Close the month: snapshot last month's report and notify."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict

from sqlalchemy import Connection, text

from pf.i18n import tr
from pf.reports.balance_sheet import balance_sheet
from pf.reports.cashflow import cashflow
from pf.reports.common import add_months, cents, month_end
from pf.reports.insights import insights


def previous_month(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    return add_months(today.strftime("%Y-%m"), -1)


def build_report(conn: Connection, month: str) -> dict[str, object]:
    f = cashflow(conn, month, month)[0]
    bs = balance_sheet(conn, month_end(month).isoformat())
    prev_nw = balance_sheet(conn, month_end(add_months(month, -1)).isoformat()).net_worth
    return {
        "month": month,
        "income": cents(f.income),
        "expenses": cents(f.expenses),
        "savings": cents(f.savings),
        "savings_rate": f.savings_rate,
        "net_worth": cents(bs.net_worth),
        "net_worth_change": cents(bs.net_worth - prev_nw),
        "expenses_by": {k: cents(v) for k, v in sorted(f.expenses_by.items())},
        "insights": [asdict(i) for i in insights(conn, month)],
    }


def snapshot_month(conn: Connection, month: str) -> dict[str, object]:
    report = build_report(conn, month)
    conn.execute(
        text(
            "INSERT INTO monthly_snapshots(month, report) VALUES (:m, :r)"
            " ON CONFLICT(month) DO UPDATE SET report = excluded.report,"
            " created_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
        ),
        {"m": month, "r": json.dumps(report, ensure_ascii=False)},
    )
    return report


def summary_line(r: dict[str, object]) -> str:
    rate = r["savings_rate"]
    rate_s = f"{rate:.0%}" if isinstance(rate, float) else "—"
    n = len(r["insights"])  # type: ignore[arg-type]
    return tr(
        "收入 {income} · 支出 {expenses} · 储蓄率 {rate} · 净资产 {nw}；{n} 条洞察",
        income=f"${float(r['income']):,.0f}",  # type: ignore[arg-type]
        expenses=f"${float(r['expenses']):,.0f}",  # type: ignore[arg-type]
        rate=rate_s,
        nw=f"${float(r['net_worth']):,.0f}",  # type: ignore[arg-type]
        n=n,
    )
