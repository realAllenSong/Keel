"""Balance sheet at any date and month-end net-worth series, valued in base currency."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import Connection, text

from pf.reports.common import Fx, infer_asset_class, month_end, month_range


@dataclass
class Line:
    account: str
    type: str  # Assets | Liabilities
    asset_class: str | None
    institution: str | None
    balance: int  # base cents, ledger sign (liabilities negative)
    note: str | None = None
    original_balances: dict[str, int] = field(default_factory=dict)


@dataclass
class BalanceSheet:
    date: str
    lines: list[Line]
    by_class: dict[str, int] = field(default_factory=dict)

    @property
    def assets(self) -> int:
        return sum(line.balance for line in self.lines if line.type == "Assets")

    @property
    def liabilities(self) -> int:
        return sum(line.balance for line in self.lines if line.type == "Liabilities")

    @property
    def net_worth(self) -> int:
        return self.assets + self.liabilities


def balance_sheet(conn: Connection, date: str, fx: Fx | None = None) -> BalanceSheet:
    fx = fx or Fx(conn)
    rows = conn.execute(
        text(
            """
            SELECT a.name, a.type, a.asset_class, a.institution, a.note,
                   vp.commodity, SUM(vp.amount) AS bal
            FROM v_postings vp JOIN accounts a ON a.id = vp.account_id
            WHERE a.type IN ('Assets', 'Liabilities') AND vp.date <= :d
              -- flow_only = wallets (支付宝/微信): spending counts, the balance is not money you hold
              AND a.flow_only = 0
            GROUP BY a.id, vp.commodity
            """
        ),
        {"d": date},
    ).all()
    acc: dict[str, Line] = {}
    for r in rows:
        line = acc.setdefault(
            r.name,
            Line(
                r.name,
                r.type,
                r.asset_class or (infer_asset_class(r.name) if r.type == "Assets" else None),
                r.institution,
                0,
                note=r.note,
            ),
        )
        line.balance += fx.to_base(r.bal, r.commodity, date)
        line.original_balances[r.commodity] = r.bal
    # A bank-wide snapshot already includes balances that can arrive later as statements.
    # Subtract those balances as of the snapshot date, keeping subsequent account movements.
    covered = conn.execute(
        text(
            """
            WITH snapshots AS (
                SELECT a.name, a.institution, MAX(v.date) AS date
                FROM valuations v JOIN accounts a ON a.name = v.account_name
                WHERE a.type = 'Assets' AND a.flow_only = 0
                  AND a.name = 'Assets:' || a.institution || :suffix
                  AND v.date <= :d
                GROUP BY a.id
            )
            SELECT s.name, vp.commodity, SUM(vp.amount) AS balance
            FROM snapshots s JOIN accounts a ON a.institution = s.institution
            JOIN v_postings vp ON vp.account_id = a.id AND vp.date <= s.date
            WHERE a.name <> s.name AND a.type = 'Assets' AND a.flow_only = 0
            GROUP BY s.name, vp.commodity
            """
        ),
        {"d": date, "suffix": ":ReportedTotal"},
    )
    for row in covered:
        line = acc.setdefault(
            row.name,
            Line(row.name, "Assets", "other", row.name.split(":")[1], 0),
        )
        line.balance -= fx.to_base(row.balance, row.commodity, date)
        line.original_balances[row.commodity] = (
            line.original_balances.get(row.commodity, 0) - row.balance
        )
    lines = sorted(
        (x for x in acc.values() if x.balance or any(x.original_balances.values())),
        key=lambda x: (x.type, x.account),
    )
    by_class: defaultdict[str, int] = defaultdict(int)
    for x in lines:
        if x.type == "Assets":
            by_class[x.asset_class or "other"] += x.balance
    return BalanceSheet(date, lines, dict(by_class))


@dataclass
class NetWorthPoint:
    month: str
    assets: int
    liabilities: int
    net_worth: int
    by_class: dict[str, int]


def networth_series(
    conn: Connection, start: str, end: str, fx: Fx | None = None
) -> list[NetWorthPoint]:
    fx = fx or Fx(conn)
    out = []
    for m in month_range(start, end):
        bs = balance_sheet(conn, month_end(m).isoformat(), fx)
        out.append(NetWorthPoint(m, bs.assets, bs.liabilities, bs.net_worth, bs.by_class))
    return out
