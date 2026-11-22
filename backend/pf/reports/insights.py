"""Monthly insights: rule-based detectors over the ledger. Each insight carries the numbers that
triggered it and a transactions filter so the UI can link straight to the evidence."""

from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import Connection, text

from pf.i18n import tr
from pf.ledger.categorize import merchant_key
from pf.ledger.core import UNCATEGORIZED_EXPENSE, UNCATEGORIZED_INCOME
from pf.ledger.reconcile import reconcile
from pf.reports.balance_sheet import balance_sheet
from pf.reports.cashflow import MonthFlow, cashflow, rollup
from pf.reports.common import (
    Assumptions,
    Fx,
    account_words,
    add_months,
    cents,
    load_assumptions,
    month_end,
)
from pf.reports.common import month_start as m_start


@dataclass
class Insight:
    kind: str
    severity: str  # info | notice | warning
    title: str
    body: str
    amount: str | None = None  # decimal string, base currency
    filters: dict[str, str] = field(default_factory=dict)  # /transactions query to show evidence


def _fmt(c: int) -> str:
    return f"${abs(c) / 100:,.0f}" if abs(c) >= 10_000 else f"${abs(c) / 100:,.2f}"


def _short(account: str) -> str:
    return " › ".join(account.split(":")[1:])


# ---------------------------------------------------------------- detectors
def category_spikes(flows: list[MonthFlow], month: str) -> list[Insight]:
    """Category (2 levels deep) well above its trailing-12-month typical level."""
    cur = next(f for f in flows if f.month == month)
    hist = [f for f in flows if f.month < month][-12:]
    if len(hist) < 3:
        return []
    out = []
    now = rollup(cur.expenses_by, 3)
    for cat, v in now.items():
        if cat.startswith(UNCATEGORIZED_EXPENSE):
            continue
        past = [rollup(f.expenses_by, 3).get(cat, 0) for f in hist]
        mean = statistics.fmean(past)
        sd = statistics.pstdev(past)
        if v - mean > max(10_000, 2 * sd, 0.5 * mean):  # >$100 and unusually high
            out.append(
                Insight(
                    "category_spike",
                    "notice",
                    tr("{cat} 支出明显偏高", cat=_short(cat)),
                    tr(
                        "本月 {v}，过去 12 个月平均 {mean}，多出 {diff}。",
                        v=_fmt(v),
                        mean=_fmt(int(mean)),
                        diff=_fmt(int(v - mean)),
                    ),
                    cents(v),
                    {"month": month, "category": cat},
                )
            )
    return sorted(out, key=lambda i: -float(i.amount or 0))[:4]


def large_one_offs(conn: Connection, month: str, fx: Fx) -> list[Insight]:
    """Unusually large single expenses from a merchant you don't use habitually.

    Baseline = all expense lines except fixed recurring charges (rent, subscriptions: same
    merchant in >= 3 months with near-constant amount); candidates = merchants seen in < 3 months.
    """
    rows = conn.execute(
        text(
            "SELECT date, description, payee, account, amount, commodity FROM v_postings"
            " WHERE account_type = 'Expenses' AND kind = 'normal' AND date BETWEEN :s AND :e"
        ),
        {"s": add_months_start(month, -12), "e": month_end(month).isoformat()},
    ).all()
    vals = {id(r): fx.to_base(r.amount, r.commodity, r.date) for r in rows}
    months_seen: defaultdict[str, set[str]] = defaultdict(set)
    amounts: defaultdict[str, list[int]] = defaultdict(list)
    for r in rows:
        k = merchant_key(r.description)
        months_seen[k].add(r.date[:7])
        amounts[k].append(vals[id(r)])

    def fixed(k: str) -> bool:
        a = amounts[k]
        return len(months_seen[k]) >= 3 and min(a) > 0 and max(a) <= 1.05 * min(a)

    baseline = sorted(vals[id(r)] for r in rows if not fixed(merchant_key(r.description)))
    if len(baseline) < 20:
        return []
    p97 = baseline[int(len(baseline) * 0.97)]
    one_off = [r for r in rows if len(months_seen[merchant_key(r.description)]) < 3]
    out = []
    for r in one_off:
        v = vals[id(r)]
        if r.date[:7] != month or v < max(50_000, p97):
            continue
        name = r.payee or r.description
        out.append(
            Insight(
                "large_one_off",
                "info",
                tr("大额支出：{name}", name=name),
                tr(
                    "{date} {v}，记在 {account}。高于过去一年 97% 的日常支出。",
                    date=r.date,
                    v=_fmt(v),
                    account=_short(r.account),
                ),
                cents(v),
                {"month": month, "q": r.description},
            )
        )
    return out[:3]


def add_months_start(month: str, n: int) -> str:
    return m_start(add_months(month, n)).isoformat()


def _recurring(conn: Connection, month: str) -> dict[str, list[tuple[str, int]]]:
    """merchant -> [(month, amount)] for merchants charged in >= 3 of the last 4 months."""
    rows = conn.execute(
        text(
            "SELECT substr(date,1,7) AS m, description, SUM(amount) AS amt FROM v_postings"
            " WHERE account_type = 'Expenses' AND kind = 'normal' AND date BETWEEN :s AND :e"
            " GROUP BY m, description"
        ),
        {"s": add_months_start(month, -5), "e": month_end(month).isoformat()},
    ).all()
    by: defaultdict[str, dict[str, int]] = defaultdict(dict)
    for r in rows:
        k = merchant_key(r.description)
        by[k][r.m] = by[k].get(r.m, 0) + r.amt
    out = {}
    last4 = [add_months(month, -i) for i in range(4)]
    for k, months in by.items():
        if sum(m in months for m in last4) >= 3:
            out[k] = sorted(months.items())
    return out


def subscriptions(conn: Connection, month: str) -> list[Insight]:
    """New recurring charges and price changes on existing ones."""
    out = []
    for k, series in _recurring(conn, month).items():
        months = dict(series)
        if month not in months:
            continue
        amounts = [a for _, a in series]
        prev_month = add_months(month, -1)
        if prev_month in months and months[month] != months[prev_month]:
            prev, now = months[prev_month], months[month]
            stable_before = len({a for m, a in series if m < month}) == 1
            if stable_before and abs(now - prev) / prev < 0.5:
                out.append(
                    Insight(
                        "price_change",
                        "notice",
                        tr("订阅涨价：{k}", k=k),
                        tr(
                            "从 {prev} 变为 {now}（{pct}），一年多花 {year}。",
                            prev=_fmt(prev),
                            now=_fmt(now),
                            pct=f"{(now - prev) / prev:+.0%}",
                            year=_fmt((now - prev) * 12),
                        ),
                        cents(now - prev),
                        {"q": k},
                    )
                )
        first = min(months)
        if (
            first >= add_months(month, -2)
            and len(months) >= 3
            and max(amounts) <= 1.15 * min(amounts)
        ):
            out.append(
                Insight(
                    "new_subscription",
                    "notice",
                    tr("新的周期性扣费：{k}", k=k),
                    tr(
                        "从 {first} 开始每月约 {m}，一年约 {y}。",
                        first=first,
                        m=_fmt(months[month]),
                        y=_fmt(months[month] * 12),
                    ),
                    cents(months[month]),
                    {"q": k},
                )
            )
    return out


def savings_trend(flows: list[MonthFlow], month: str) -> list[Insight]:
    cur = next(f for f in flows if f.month == month)
    hist = [f for f in flows if f.month < month and f.income > 0][-12:]
    if cur.savings_rate is None or len(hist) < 3:
        return []
    avg = statistics.fmean(f.savings_rate or 0 for f in hist)
    diff = cur.savings_rate - avg
    if abs(diff) < 0.10:
        return []
    up = diff > 0
    return [
        Insight(
            "savings_rate",
            "info" if up else "warning",
            tr("储蓄率高于平时") if up else tr("储蓄率低于平时"),
            tr(
                "本月储蓄率 {now}，过去 12 个月平均 {avg}。",
                now=f"{cur.savings_rate:.0%}",
                avg=f"{avg:.0%}",
            ),
            cents(cur.savings),
            {"month": month},
        )
    ]


def idle_cash(
    conn: Connection, month: str, flows: list[MonthFlow], a: Assumptions, fx: Fx
) -> list[Insight]:
    bs = balance_sheet(conn, month_end(month).isoformat(), fx)
    cash = bs.by_class.get("cash", 0)
    last = [f for f in flows if f.month <= month][-12:]
    monthly_spend = statistics.fmean(f.expenses for f in last) if last else 0
    excess = cash - a.idle_cash_months * monthly_spend
    if monthly_spend <= 0 or excess < 500_000:
        return []
    cost = excess * a.tbill_rate
    return [
        Insight(
            "idle_cash",
            "warning",
            tr("闲置现金偏多"),
            tr(
                "现金 {cash}，约 {months} 个月开销。超出 {n} 个月应急金的 {excess} 若放进货币基金或短期国债（约 {rate}），一年约多 {gain}。",
                cash=_fmt(cash),
                months=f"{cash / monthly_spend:.1f}",
                n=f"{a.idle_cash_months:g}",
                excess=_fmt(int(excess)),
                rate=f"{a.tbill_rate:.1%}",
                gain=_fmt(int(cost)),
            ),
            cents(int(cost)),
        )
    ]


def networth_change(conn: Connection, month: str, flows: list[MonthFlow], fx: Fx) -> list[Insight]:
    prev = balance_sheet(conn, month_end(add_months(month, -1)).isoformat(), fx).net_worth
    now = balance_sheet(conn, month_end(month).isoformat(), fx).net_worth
    cur = next(f for f in flows if f.month == month)
    delta = now - prev
    market = delta - cur.savings
    sign = lambda v: ("+" if v >= 0 else "−") + _fmt(v)  # noqa: E731
    body = tr(
        "本月净资产 {delta}：储蓄贡献 {savings}", delta=sign(delta), savings=sign(cur.savings)
    )
    if market:
        body += tr("，市场与估值变化 {m}", m=sign(market))
    return [Insight("networth_change", "info", tr("净资产变化"), body + tr("。"), cents(delta))]


def data_health(conn: Connection, month: str) -> list[Insight]:
    out = []
    pending = conn.execute(
        text("SELECT COUNT(*) FROM transactions WHERE status='pending' AND sealed=1")
    ).scalar_one()
    unc = conn.execute(
        text(
            "SELECT COUNT(DISTINCT txn_id) FROM v_postings WHERE account IN (:a, :b)"
            " AND date BETWEEN :s AND :e"
        ),
        {
            "a": UNCATEGORIZED_EXPENSE,
            "b": UNCATEGORIZED_INCOME,
            "s": m_start(month).isoformat(),
            "e": month_end(month).isoformat(),
        },
    ).scalar_one()
    if unc:
        out.append(
            Insight(
                "uncategorized",
                "warning",
                tr("本月还有 {n} 笔未分类", n=unc),
                tr("未分类的支出会让报表失真，在「明细 → 交易」里点一下就能归类。"),
                filters={"month": month, "status": "pending"},
            )
        )
    elif pending:
        out.append(
            Insight(
                "pending",
                "info",
                tr("{n} 笔建议分类待确认", n=pending),
                tr("确认后它们会作为规则长期生效。"),
            )
        )
    if issues := reconcile(conn):
        out.append(
            Insight(
                "reconcile",
                "warning",
                tr("{n} 处对账不符", n=len(issues)),
                tr("账本余额与账单余额不一致，可能漏导了某段时间的流水。"),
            )
        )
    # Data is complete through its last statement, sync or hand-checked balance; a month still in
    # progress only needs it through about today.
    from pf.ledger.onboarding import coverage_through

    cut = (min(month_end(month), dt.date.today()) - dt.timedelta(days=10)).isoformat()
    names = conn.execute(
        text("SELECT DISTINCT a.name FROM import_files f JOIN accounts a ON a.id = f.account_id")
    ).scalars()
    stale = [
        _Stale(name, last)
        for name in names
        if (last := coverage_through(conn, name)) is not None and last < cut
    ]
    for s in stale:
        out.append(
            Insight(
                "stale_import",
                "notice",
                tr("{name} 数据只到 {last}", name=account_words(s.name, None), last=s.last),
                tr("下载 {last} 之后的账单并拖进 Keel 窗口，本月报表才完整。", last=s.last),
            )
        )
    return out


@dataclass
class _Stale:
    name: str
    last: str


SEVERITY = {"warning": 0, "notice": 1, "info": 2}


def insights(conn: Connection, month: str, a: Assumptions | None = None) -> list[Insight]:
    a = a or load_assumptions()
    fx = Fx(conn, a.base_currency)
    flows = cashflow(conn, add_months(month, -12), month, fx)
    found: list[Insight] = []
    found += data_health(conn, month)
    found += networth_change(conn, month, flows, fx)
    found += category_spikes(flows, month)
    found += subscriptions(conn, month)
    found += large_one_offs(conn, month, fx)
    found += savings_trend(flows, month)
    found += idle_cash(conn, month, flows, a, fx)
    return sorted(found, key=lambda i: SEVERITY[i.severity])
