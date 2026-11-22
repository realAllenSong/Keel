"""Everything the home screen needs in one call: the one-sentence month, the voyage (wake, boat,
lighthouses, wind, current) and a short, ranked list of things the user should do."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection, text

from pf.api.deps import get_conn
from pf.i18n import tr
from pf.ledger.close import CloseStatus, close_month, close_status, reopen_month
from pf.ledger.core import UNCATEGORIZED_EXPENSE, UNCATEGORIZED_INCOME
from pf.ledger.onboarding import checklist, coverage_through
from pf.ledger.reconcile import reconcile
from pf.reports.balance_sheet import networth_series
from pf.reports.cashflow import cashflow
from pf.reports.common import account_words, add_months, cents, latest_month
from pf.reports.insights import insights
from pf.reports.lighthouses import lighthouses

router = APIRouter(prefix="/api", tags=["home"])
Conn = Annotated[Connection, Depends(get_conn)]

SHORT = {1: "收益 ≥ 储蓄", 2: "收益 ≥ 开销", 3: "财务独立"}  # translated where used


class Sentence(BaseModel):
    month: str
    income: str
    expenses: str
    savings: str
    savings_rate: float | None
    net_worth: str
    net_worth_change: str


class Lighthouse(BaseModel):
    id: int
    title: str
    short: str
    target: str
    progress: float
    reached: bool
    years_to_go: float | None


class WakePoint(BaseModel):
    month: str
    net_worth: str


class Voyage(BaseModel):
    investable: str
    wind: str  # annual savings (trailing 12m)
    current: str  # expected annual passive income
    expected_return: float
    wake: list[WakePoint]
    lighthouses: list[Lighthouse]


class Todo(BaseModel):
    kind: Literal[
        "files", "review", "stale", "balance", "reconcile", "insight", "close", "close_drift"
    ]
    title: str
    detail: str | None = None
    action: str
    href: str | None = None  # in-app route ("#pending" opens the pending-files sheet)
    external: str | None = None  # bank export page
    count: int = 1


class FirstRunStep(BaseModel):
    id: str
    label: str
    done: bool


class HomeOut(BaseModel):
    has_data: bool
    sentence: Sentence | None
    voyage: Voyage | None
    todos: list[Todo]
    more_todos: int
    first_run: list[FirstRunStep]


def _todos(conn: Connection, month: str | None) -> list[Todo]:
    out: list[Todo] = []
    # Pending statements are presented per ACCOUNT (73 files are usually 3 accounts).
    accounts = conn.execute(
        text(
            "SELECT COUNT(DISTINCT COALESCE(importer,'') || '|' || COALESCE(hint,'')) AS accounts,"
            " COUNT(*) AS files FROM pending_imports WHERE reason = 'needs_account'"
        )
    ).one()
    unknown = conn.execute(
        text("SELECT COUNT(*) FROM pending_imports WHERE reason = 'unknown_format'")
    ).scalar_one()
    locked = conn.execute(
        text("SELECT COUNT(*) FROM pending_imports WHERE reason = 'needs_password'")
    ).scalar_one()
    if accounts.files or unknown or locked:
        parts = []
        if accounts.files:
            parts.append(
                tr("发现 {a} 个新账户（{f} 份账单）", a=accounts.accounts, f=accounts.files)
            )
        if unknown:
            parts.append(tr("{n} 份新格式的账单", n=unknown))
        if locked:
            parts.append(tr("{n} 个账单压缩包等解压密码", n=locked))
        detail = tr("；").join(
            tr(d)
            for d, on in (
                ("确认一下账户就能入账", accounts.files),
                ("新格式点几下教会它", unknown),
                ("密码在支付宝 / 微信的消息里", locked),
            )
            if on
        )
        out.append(
            Todo(
                kind="files",
                title=tr("，").join(parts),
                detail=detail,
                action=tr("处理"),
                href="#pending",
                count=accounts.files + unknown + locked,
            )
        )

    pending = conn.execute(
        text(
            """SELECT
                 SUM(CASE WHEN t.kind = 'normal' AND a.name IN (:ue, :ui) THEN 1 ELSE 0 END) AS unknown,
                 COUNT(DISTINCT t.id) AS total
               FROM transactions t JOIN postings p ON p.txn_id = t.id JOIN accounts a ON a.id = p.account_id
               WHERE t.sealed = 1 AND t.status = 'pending'"""
        ),
        {"ue": UNCATEGORIZED_EXPENSE, "ui": UNCATEGORIZED_INCOME},
    ).one()
    if pending.total:
        unknown = pending.unknown or 0
        title = (
            tr("{n} 笔交易不知道是什么", n=unknown)
            if unknown
            else tr("{n} 笔交易等你确认分类", n=pending.total)
        )
        detail = (
            tr("另有 {n} 笔已有建议，一键确认即可", n=pending.total - unknown)
            if unknown and pending.total > unknown
            else None
        )
        out.append(
            Todo(
                kind="review",
                title=title,
                detail=detail,
                action=tr("处理"),
                href="/details?tab=transactions&status=pending",
                count=pending.total,
            )
        )

    if month:
        for item in checklist(conn, month):
            if item.status == "ok":
                continue
            name = item.institution or item.account.split(":")[1]
            since = coverage_through(conn, item.account) or item.period_end
            out.append(
                Todo(
                    kind="stale",
                    title=(
                        tr("{name} 还缺 {since} 之后的账单", name=name, since=since)
                        if since
                        else tr("{name} 还缺账单", name=name)
                    ),
                    detail=_stale_detail(since),
                    action=tr("打开导出页") if item.export_url else tr("去导入"),
                    external=item.export_url,
                    href=None if item.export_url else "#pending",
                )
            )

    for name, institution in _unchecked_accounts(conn):
        what = account_words(name, institution)
        out.append(
            Todo(
                kind="balance",
                title=tr("{name} 还没核对过余额", name=what),
                detail=tr("这份账单里没有余额。在银行 App 里看一眼现在的余额填进来，账才对得上。"),
                action=tr("核对"),
                href="/details?tab=assets",
            )
        )

    if issues := reconcile(conn):
        out.append(
            Todo(
                kind="reconcile",
                title=tr("{n} 处余额和账单对不上", n=len(issues)),
                detail=tr("可能漏导了某段时间的流水"),
                action=tr("查看"),
                href="/details?tab=assets",
                count=len(issues),
            )
        )
    if month:
        from pf.prefs import load_prefs

        status = close_status(conn, month)
        # A ready month closes by itself at the next daily run: nothing to ask the user for.
        if not (load_prefs(conn).auto_close and status.closed is None):
            out.extend(_close_todo(status))
        for i in insights(conn, month):
            if i.severity == "warning" and i.kind not in (
                "uncategorized",
                "reconcile",
                "stale_import",
            ):
                out.append(
                    Todo(
                        kind="insight",
                        title=i.title,
                        detail=i.body,
                        action=tr("看看"),
                        href="/details?tab=spending",
                    )
                )
    return out


def _unchecked_accounts(conn: Connection) -> list[tuple[str, str | None]]:
    """Imported accounts Keel has never seen a balance for (a statement balance, a sync or one
    you entered): their running total starts from an unknown point, so it can't be trusted yet."""
    rows = conn.execute(
        text(
            """SELECT a.name, a.institution FROM accounts a
               WHERE a.type IN ('Assets', 'Liabilities') AND a.flow_only = 0
                 AND EXISTS (SELECT 1 FROM import_files f WHERE f.account_id = a.id)
                 AND NOT EXISTS (SELECT 1 FROM import_files f WHERE f.account_id = a.id
                                 AND f.importer = 'connector')  -- a sync reports the balance
                 AND NOT EXISTS (SELECT 1 FROM balance_assertions b WHERE b.account_id = a.id)
                 AND NOT EXISTS (SELECT 1 FROM v_postings p WHERE p.account_id = a.id
                                 AND p.kind IN ('opening', 'adjustment'))
               ORDER BY a.name"""
        )
    ).all()
    return [(r.name, r.institution) for r in rows]


def _stale_detail(since: str | None) -> str:
    days = (dt.date.today() - dt.date.fromisoformat(since)).days if since else None
    hint = tr("在银行网站下载后，应用会自动识别")
    if days is not None and days > 35:
        return tr("已经 {n} 天没有新数据了。", n=days) + hint
    return hint


def _month_name(month: str) -> str:
    return tr("{m} 月", m=int(month[5:]))


def _close_todo(s: CloseStatus) -> list[Todo]:
    """Ready and not closed: offer to close. Closed but the numbers moved since: say by how much."""
    if s.closed is None:
        if not s.ready or s.month + "-28" > dt.date.today().isoformat():
            return []
        return [
            Todo(
                kind="close",
                title=tr("{m}的账都齐了，可以结账", m=_month_name(s.month)),
                detail=tr("账户都已更新，余额对得上，没有待确认的交易"),
                action=tr("结账"),
                href=f"#close?month={s.month}",
            )
        ]
    if s.drift_income == 0 and s.drift_expenses == 0:
        return []
    parts = []
    if s.drift_expenses:
        parts.append(tr("支出 {d}", d=_signed(s.drift_expenses, s.closed.currency)))
    if s.drift_income:
        parts.append(tr("收入 {d}", d=_signed(s.drift_income, s.closed.currency)))
    return [
        Todo(
            kind="close_drift",
            title=tr(
                "已结账的{m}有变动：{what}", m=_month_name(s.month), what=tr("，").join(parts)
            ),
            detail=tr("银行补记了晚到的交易，或你改了分类。看一眼没问题就重新结账"),
            action=tr("重新结账"),
            href=f"#close?month={s.month}",
        )
    ]


def _signed(minor: int, currency: str) -> str:
    symbol = {"USD": "$", "CNY": "¥"}.get(currency, currency + " ")
    return f"{'+' if minor > 0 else '−'}{symbol}{abs(minor) / 100:,.0f}"


class RevisionOut(BaseModel):
    revision: str


@router.get("/revision", response_model=RevisionOut)
def revision() -> RevisionOut:
    """Changes whenever the ledger does, whoever changed it (see pf.db.ledger_revision). Cheap:
    the app asks every few seconds and reloads its screens when the answer is new."""
    from pf.db import ledger_revision

    return RevisionOut(revision=ledger_revision())


@router.get("/home", response_model=HomeOut)
def home(conn: Conn, month: str | None = None) -> HomeOut:
    last = latest_month(conn)
    has_data = last is not None
    # An explicitly selected current month may not have imports yet. Keep that period and
    # report zero cashflow, rather than silently showing an older month's income/spending.
    if month and not last:
        month = None
    month = month or (add_months(last, -1) if last else None)  # default: last COMPLETE month
    todos = _todos(conn, month)
    n_accounts = conn.execute(text("SELECT COUNT(*) FROM import_files")).scalar_one()
    n_assertions = conn.execute(text("SELECT COUNT(*) FROM balance_assertions")).scalar_one()
    n_pending = conn.execute(
        text("SELECT COUNT(*) FROM transactions WHERE status = 'pending' AND sealed = 1")
    ).scalar_one()
    first_run = [
        FirstRunStep(id="import", label=tr("把第一份账单拖进来"), done=n_accounts > 0),
        FirstRunStep(
            id="balance",
            label=tr("核对一次账户余额"),
            done=n_assertions > 0 and not _unchecked_accounts(conn),
        ),
        FirstRunStep(
            id="review", label=tr("确认看不懂的交易"), done=n_accounts > 0 and n_pending == 0
        ),
    ]
    if not month:
        return HomeOut(
            has_data=False,
            sentence=None,
            voyage=None,
            todos=todos[:3],
            more_todos=max(0, len(todos) - 3),
            first_run=first_run,
        )

    f = cashflow(conn, month, month)[0]
    series = networth_series(conn, add_months(month, -35), month)
    nw_now = series[-1].net_worth
    nw_prev = series[-2].net_worth if len(series) > 1 else 0
    lh = lighthouses(conn, month)
    return HomeOut(
        has_data=has_data,
        sentence=Sentence(
            month=month,
            income=cents(f.income),
            expenses=cents(f.expenses),
            savings=cents(f.savings),
            savings_rate=f.savings_rate,
            net_worth=cents(nw_now),
            net_worth_change=cents(nw_now - nw_prev),
        ),
        voyage=Voyage(
            investable=cents(lh.investable),
            wind=cents(lh.annual_savings),
            current=cents(lh.expected_income),
            expected_return=lh.expected_return,
            wake=[WakePoint(month=p.month, net_worth=cents(p.net_worth)) for p in series],
            lighthouses=[
                Lighthouse(
                    id=x.id,
                    title=x.title,
                    short=tr(SHORT[x.id]),
                    target=cents(x.target),
                    progress=x.progress,
                    reached=x.reached,
                    years_to_go=x.years_to_go,
                )
                for x in lh.lighthouses
            ],
        ),
        todos=todos[:3],
        more_todos=max(0, len(todos) - 3),
        first_run=first_run,
    )


class CloseCheckOut(BaseModel):
    id: str
    ok: bool
    count: int
    label: str
    detail: str | None


class CloseOut(BaseModel):
    month: str
    ready: bool
    closed_at: str | None
    currency: str
    income: str
    expenses: str
    drift_income: str
    drift_expenses: str
    checks: list[CloseCheckOut]


_CHECK_LABELS = {
    "accounts": ("每个账户都更新到月底", "{n} 个账户还缺数据：{items}"),
    "balances": ("余额和银行对得上", "{n} 处对不上：{items}"),
    "review": ("没有待确认的交易", "还有 {n} 笔等你确认"),
}


def _close_out(s: CloseStatus) -> CloseOut:
    currency = s.closed.currency if s.closed else "USD"

    def money(minor: int) -> str:
        return f"{minor / 100:.2f}"

    checks = []
    for c in s.checks:
        label, detail = _CHECK_LABELS[c.id]
        checks.append(
            CloseCheckOut(
                id=c.id,
                ok=c.ok,
                count=c.count,
                label=tr(label),
                detail=None if c.ok else tr(detail, n=c.count, items=tr("、").join(c.items)),
            )
        )
    return CloseOut(
        month=s.month,
        ready=s.ready,
        closed_at=s.closed.closed_at if s.closed else None,
        currency=currency,
        income=money(s.income),
        expenses=money(s.expenses),
        drift_income=money(s.drift_income),
        drift_expenses=money(s.drift_expenses),
        checks=checks,
    )


class CloseIn(BaseModel):
    month: str


@router.get("/close", response_model=CloseOut)
def get_close(conn: Conn, month: str) -> CloseOut:
    return _close_out(close_status(conn, month))


@router.post("/close", response_model=CloseOut)
def post_close(body: CloseIn, conn: Conn) -> CloseOut:
    try:
        return _close_out(close_month(conn, body.month))
    except ValueError as e:
        msg = {
            "month_not_over": tr("这个月还没过完，不能结账"),
            "month_not_ready": tr("还有没处理完的事，不能结账"),
        }.get(str(e), str(e))
        raise HTTPException(409, msg) from e


@router.delete("/close/{month}", response_model=CloseOut)
def delete_close(month: str, conn: Conn) -> CloseOut:
    reopen_month(conn, month)
    return _close_out(close_status(conn, month))
