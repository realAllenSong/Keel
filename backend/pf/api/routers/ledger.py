from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import tempfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import Connection, text

from pf.api.deps import get_conn
from pf.api.schemas import (
    Account,
    CategorizeIn,
    CategorizeOut,
    Discrepancy,
    EditIn,
    IdsIn,
    ImportedFile,
    ImportResult,
    ManualIn,
    ReportCurrency,
    ReviewSummary,
    RuleIn,
    RuleOut,
    SplitIn,
    Txn,
    TxnPage,
    TxnPart,
)
from pf.db import make_engine
from pf.i18n import tr
from pf.importers.base import ImportError_
from pf.ledger import categorize as cat
from pf.ledger import edits, tags
from pf.ledger.categorize import _seed
from pf.ledger.core import UNCATEGORIZED_EXPENSE, UNCATEGORIZED_INCOME, LedgerError
from pf.ledger.ownflows import match_own_flows
from pf.ledger.reconcile import reconcile
from pf.money import from_minor
from pf.reports.common import Fx, cents, month_rate_date

router = APIRouter(prefix="/api", tags=["ledger"])
Conn = Annotated[Connection, Depends(get_conn)]

# One row per sealed transaction: its "main" leg (statement line, else an asset/liability leg)
# and the other leg (category, or receiving account for transfers).
TXN_SQL = """
SELECT * FROM (
  SELECT t.id, t.date, t.description, t.payee, t.status, t.kind, t.category_source, t.note,
         a.name AS account, p.amount, p.commodity, c.precision,
         (SELECT a2.name FROM postings p2 JOIN accounts a2 ON a2.id = p2.account_id
           WHERE p2.txn_id = t.id AND p2.id != p.id ORDER BY p2.id LIMIT 1) AS counter_account
  FROM transactions t
  JOIN postings p ON p.id = (
      SELECT p1.id FROM postings p1 JOIN accounts a1 ON a1.id = p1.account_id
      WHERE p1.txn_id = t.id
      ORDER BY (p1.source_key IS NULL), (a1.type NOT IN ('Assets','Liabilities')), p1.id LIMIT 1)
  JOIN accounts a ON a.id = p.account_id
  JOIN commodities c ON c.code = p.commodity
  WHERE t.sealed = 1
) x
"""


def _txn(r) -> Txn:  # type: ignore[no-untyped-def]
    return Txn(
        id=r.id,
        date=r.date,
        description=r.description,
        payee=r.payee,
        account=r.account,
        counter_account=r.counter_account,
        amount=str(from_minor(r.amount, r.precision)),
        commodity=r.commodity,
        status=r.status,
        kind=r.kind,
        category_source=r.category_source,
        note=r.note,
    )


@router.get("/accounts", response_model=list[Account])
def accounts(conn: Conn) -> list[Account]:
    rows = conn.execute(
        text(
            """
            SELECT a.name, a.type, a.institution, a.asset_class, a.currency, a.flow_only,
                   COALESCE(SUM(vp.amount), 0) AS bal,
                   COALESCE(vp.commodity, a.currency, 'USD') AS cm,
                   COALESCE(MAX(vp.precision), 2) AS prec
            FROM accounts a LEFT JOIN v_postings vp ON vp.account_id = a.id
            GROUP BY a.id, vp.commodity ORDER BY a.name
            """
        )
    ).all()
    return [
        Account(
            name=r.name,
            type=r.type,
            institution=r.institution,
            asset_class=r.asset_class,
            currency=r.currency,
            balance=str(from_minor(r.bal, r.prec)),
            commodity=r.cm,
            flow_only=bool(r.flow_only),
        )
        for r in rows
    ]


@router.get("/categories", response_model=list[str])
def categories(conn: Conn) -> list[str]:
    used = {
        r.name
        for r in conn.execute(
            text(
                "SELECT name FROM accounts"
                " WHERE type IN ('Expenses','Income','Assets','Liabilities')"
            )
        )
    }
    seed_rules, hints = _seed()
    defaults = (
        {r.target for r in seed_rules}
        | set(hints.values())
        | {
            "Expenses:Housing:Rent",
            "Expenses:Health",
            "Expenses:Insurance",
            "Expenses:Taxes",
            "Expenses:Gifts",
            "Expenses:Personal",
            "Expenses:Entertainment",
            "Income:Salary",
            "Income:Bonus",
            "Income:Dividends",
            "Income:Refunds",
            "Income:Family Support",  # money from family (e.g. tuition and living costs): not salary
            "Income:Reimbursements",  # friends paying back their share, employer reimbursements
            "Income:Other",
            ELSEWHERE,  # your own account Keel has no statements for (a brokerage, another bank)
        }
    )
    return sorted((used | defaults) - {UNCATEGORIZED_EXPENSE, UNCATEGORIZED_INCOME})


@router.get("/transactions", response_model=TxnPage)
def transactions(
    conn: Conn,
    month: str | None = None,
    account: str | None = None,
    category: str | None = None,
    q: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    tag: str | None = None,
    end_date: dt.date | None = None,
    limit: int = 200,
    offset: int = 0,
    currency: ReportCurrency = "USD",
    cashflow_only: bool = False,
    include_summary: bool = False,
) -> TxnPage:
    where: list[str] = ["1=1"]
    params: dict[str, object] = {"limit": min(limit, 2000), "offset": offset}
    through = end_date.isoformat() if end_date else None
    category_type = category.partition(":")[0] if category else None
    flow_category = category_type in ("Income", "Expenses")
    flow_filter = None
    flow_sign = 1 if category_type == "Expenses" else -1
    if month:
        where.append("date LIKE :month")
        params["month"] = f"{month}%"
    if through:
        where.append("date <= :end_date")
        params["end_date"] = through
    if account:
        where.append("(account = :acc OR counter_account = :acc)")
        params["acc"] = account
    if category:
        params["cat"] = category
        if not flow_category:
            where.append(
                "id IN (SELECT cp.txn_id FROM postings cp JOIN accounts ca ON ca.id = cp.account_id"
                " WHERE ca.name = :cat OR instr(ca.name, :cat || ':') = 1)"
            )
    if flow_category or cashflow_only:
        flow_filter = "vp.account_type IN ('Income', 'Expenses')"
        if flow_category:
            flow_filter += " AND (vp.account = :cat OR instr(vp.account, :cat || ':') = 1)"
        where.append("kind NOT IN ('adjustment', 'opening')")
        where.append(
            f"EXISTS (SELECT 1 FROM v_postings vp WHERE vp.txn_id = x.id AND {flow_filter})"
        )
    if q:
        where.append("(description LIKE :q OR payee LIKE :q OR note LIKE :q)")
        params["q"] = f"%{q}%"
    if status:
        where.append("status = :status")
        params["status"] = status
    if kind:
        where.append("kind = :kind")
        params["kind"] = kind
    if tag:
        where.append(f"id IN ({tags.TAGGED})")
        params["tag"] = tags.clean(tag)
    filtered = f"{TXN_SQL} WHERE {' AND '.join(where)}"
    total = conn.execute(text(f"SELECT COUNT(*) FROM ({filtered})"), params).scalar_one()
    rows = conn.execute(
        text(f"{filtered} ORDER BY date DESC, id DESC LIMIT :limit OFFSET :offset"),
        params,
    ).all()
    items = [_txn(r) for r in rows]
    _with_parts(conn, items)
    tagged = tags.tags_for(conn, [t.id for t in items])
    for t in items:
        t.tags = tagged.get(t.id, [])

    fx = Fx(conn, currency)
    usd = fx if currency == "USD" else Fx(conn)
    display_total, original_totals = None, {}
    if include_summary and offset == 0:
        display_total, original_totals = _transaction_summary(
            conn, filtered, params, fx, through, flow_filter=flow_filter, flow_sign=flow_sign
        )

    if flow_filter:
        _with_report_amounts(conn, items, flow_filter, flow_sign, params, fx, through)

    missing = set(fx.missing)
    for item in items:
        date = month_rate_date(item.date[:7], through)
        base_rate = usd.rate(item.commodity, date)
        display_rate = fx.rate(item.commodity, date)
        item.base_rate = str(base_rate) if base_rate is not None else None
        item.display_currency = currency
        item.display_rate = str(display_rate) if display_rate is not None else None
        if display_rate is None and not flow_filter:
            missing.add(item.commodity)
    return TxnPage(
        items=items,
        total=total,
        currency=currency,
        display_total=display_total,
        original_totals=original_totals,
        missing_fx=sorted(missing),
    )


def _with_report_amounts(
    conn: Connection,
    items: list[Txn],
    flow_filter: str,
    flow_sign: int,
    params: dict[str, object],
    fx: Fx,
    through: str | None,
) -> None:
    if not items:
        return
    amounts: defaultdict[int, dict[str, Decimal]] = defaultdict(dict)
    for row in conn.execute(
        text(
            "SELECT vp.txn_id, vp.commodity, vp.precision, SUM(vp.amount) AS amount"
            " FROM v_postings vp WHERE vp.txn_id IN (SELECT value FROM json_each(:ids))"
            f" AND {flow_filter} GROUP BY vp.txn_id, vp.commodity"
        ),
        {**params, "ids": json.dumps([item.id for item in items])},
    ):
        amounts[row.txn_id][row.commodity] = from_minor(flow_sign * row.amount, row.precision)
    for item in items:
        native = amounts[item.id]
        item.report_original_amounts = {code: str(amount) for code, amount in native.items()}
        date = month_rate_date(item.date[:7], through)
        converted = Decimal(0)
        complete = True
        for code, amount in native.items():
            if not amount:
                continue
            rate = fx.rate(code, date)
            if rate is None:
                fx.missing.add(code)
                complete = False
            else:
                converted += amount * rate
        if complete:
            item.report_display_amount = str(converted)


def _transaction_summary(
    conn: Connection,
    filtered: str,
    params: dict[str, object],
    fx: Fx,
    through: str | None,
    *,
    flow_filter: str | None,
    flow_sign: int,
) -> tuple[str | None, dict[str, str]]:
    if flow_filter:
        # Match cashflow(): value each monthly category and commodity subtotal before rollup.
        query = f"""
            SELECT substr(vp.date, 1, 7) AS month, vp.commodity, vp.precision,
                   SUM(vp.amount) AS amount
            FROM v_postings vp JOIN ({filtered}) f ON f.id = vp.txn_id
            WHERE {flow_filter}
            GROUP BY month, vp.account, vp.commodity
        """
        sign = flow_sign
    else:
        query = f"""
            SELECT substr(date, 1, 7) AS month, commodity, precision, SUM(amount) AS amount
            FROM ({filtered})
            GROUP BY month, account, commodity
        """
        sign = 1

    total = 0
    original: defaultdict[str, int] = defaultdict(int)
    precisions: dict[str, int] = {}
    for row in conn.execute(text(query), params):
        total += sign * fx.to_base(row.amount, row.commodity, month_rate_date(row.month, through))
        original[row.commodity] += sign * row.amount
        precisions[row.commodity] = row.precision
    return (
        None if fx.missing else cents(total),
        {
            commodity: str(from_minor(amount, precisions[commodity]))
            for commodity, amount in original.items()
        },
    )


def _with_parts(conn: Connection, items: list[Txn]) -> None:
    """Fill `parts` for split lines and `edited` for lines you changed."""
    if not items:
        return
    ids = json.dumps([t.id for t in items])
    parts: dict[int, list[TxnPart]] = {}
    for r in conn.execute(
        text(
            "SELECT p.txn_id, a.name, p.amount, c.precision FROM postings p"
            " JOIN accounts a ON a.id = p.account_id JOIN commodities c ON c.code = p.commodity"
            " WHERE p.source_key IS NULL AND p.txn_id IN (SELECT value FROM json_each(:ids))"
            " AND (SELECT COUNT(*) FROM postings WHERE txn_id = p.txn_id) > 2 ORDER BY p.id"
        ),
        {"ids": ids},
    ):
        parts.setdefault(r.txn_id, []).append(
            TxnPart(account=r.name, amount=str(from_minor(r.amount, r.precision)))
        )
    edited = {
        r.txn_id
        for r in conn.execute(
            text(
                "SELECT p.txn_id FROM postings p JOIN accounts a ON a.id = p.account_id"
                " JOIN line_edits e ON e.account_name = a.name AND e.source_key = p.source_key"
                " WHERE p.txn_id IN (SELECT value FROM json_each(:ids))"
            ),
            {"ids": ids},
        )
    }
    for t in items:
        t.parts = parts.get(t.id, [])
        t.edited = t.id in edited


class ReviewGroup(BaseModel):
    hint: str | None = None  # what this group probably is and the easiest way to settle it
    key: str
    direction: str  # out | in
    count: int
    total: str  # decimal, statement-side sign
    accounts: list[str]
    samples: list[str]
    txn_ids: list[int]
    first: str
    last: str


class OkCount(BaseModel):
    count: int


class GroupDecision(BaseModel):
    txn_ids: list[int]
    category: str
    remember: bool = True


ELSEWHERE = "Assets:Elsewhere"
_CARD_PAYMENT = re.compile(
    r"\bcrd\b|credit card|card ?payment|autopay|epay|payment to (?:crd|card)|applecard|discover|capital one|citi card",
    re.I,
)
_BROKERAGE = re.compile(
    r"vanguard|fidelity|schwab|robinhood|wealthfront|betterment|e\*?trade|brokerage|buy investment|moneyline",
    re.I,
)


def _group_hint(key: str, direction: str) -> str | None:
    """Two things every new ledger meets: paying a card and funding a brokerage Keel can't see yet."""
    if direction == "out" and _CARD_PAYMENT.search(key):
        return tr("像是在还另一张信用卡。把那张卡的账单也拖进来，Keel 会自动把两边配对，不用分类。")
    if _BROKERAGE.search(key):
        return tr(
            "像是转进或转出你的投资账户。导入那个账户的账单会自动配对；不打算导入，就选「我的其他账户（未导入）」。"
        )
    return None


@router.get("/review/groups", response_model=list[ReviewGroup])
def review_groups(conn: Conn) -> list[ReviewGroup]:
    """Lines nobody could categorise, one row per merchant or person."""
    from pf.ledger.groups import uncategorized_groups

    return [
        ReviewGroup(
            hint=_group_hint(g.key, g.direction),
            key=g.key,
            direction=g.direction,
            count=g.count,
            total=str(Decimal(g.total).scaleb(-2)),
            accounts=sorted(g.accounts),
            samples=g.samples,
            txn_ids=g.txn_ids,
            first=g.first,
            last=g.last,
        )
        for g in uncategorized_groups(conn)
    ]


@router.post("/review/groups/apply", response_model=OkCount)
def apply_group(body: GroupDecision, conn: Conn) -> OkCount:
    from pf.ledger.groups import categorize_group

    return OkCount(
        count=categorize_group(conn, body.txn_ids, body.category, remember=body.remember)
    )


@router.get("/review", response_model=list[Txn])
def review(conn: Conn) -> list[Txn]:
    rows = conn.execute(
        text(
            f"{TXN_SQL} WHERE status = 'pending' ORDER BY"
            " (counter_account NOT IN (:ue, :ui)), kind = 'transfer', abs(amount) DESC, date DESC"
        ),
        {"ue": UNCATEGORIZED_EXPENSE, "ui": UNCATEGORIZED_INCOME},
    ).all()
    return [_txn(r) for r in rows]


@router.get("/review/summary", response_model=ReviewSummary)
def review_summary(conn: Conn) -> ReviewSummary:
    r = conn.execute(
        text(
            f"""SELECT
              SUM(kind = 'normal' AND counter_account IN (:ue, :ui)) AS unc,
              SUM(kind = 'normal' AND counter_account NOT IN (:ue, :ui)) AS sug,
              SUM(kind = 'transfer') AS tr
            FROM ({TXN_SQL}) WHERE status = 'pending'"""
        ),
        {"ue": UNCATEGORIZED_EXPENSE, "ui": UNCATEGORIZED_INCOME},
    ).one()
    return ReviewSummary(
        uncategorized=r.unc or 0, suggested=r.sug or 0, possible_transfers=r.tr or 0
    )


@router.post("/transactions/{txn_id}/category", response_model=CategorizeOut)
def set_category(txn_id: int, body: CategorizeIn, conn: Conn) -> CategorizeOut:
    try:
        n = cat.set_category(
            conn,
            txn_id,
            body.category,
            payee=body.payee,
            remember=body.remember,
            rule_pattern=body.pattern,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return CategorizeOut(also_updated=n)


@router.post("/transactions/confirm")
def confirm(body: IdsIn, conn: Conn) -> dict[str, int]:
    cat.confirm(conn, body.ids)
    return {"confirmed": len(body.ids)}


class TagsIn(BaseModel):
    tags: list[str]


class TagApplyIn(BaseModel):
    ids: list[int]
    tag: str
    remove: bool = False


class TagTotalOut(BaseModel):
    tag: str
    count: int
    spent: str
    income: str
    first: str
    last: str


@router.get("/tags", response_model=list[TagTotalOut])
def list_tags(conn: Conn, currency: ReportCurrency = "USD") -> list[TagTotalOut]:
    """Every tag with what was spent and received under it."""
    return [TagTotalOut(**t.__dict__) for t in tags.summary(conn, currency)]


class TagRuleIn(BaseModel):
    pattern: str = ""
    tag: str
    txn_id: int | None = None  # instead of a pattern: this line's merchant


class TagRuleOut(BaseModel):
    id: int
    pattern: str
    tag: str


@router.get("/tags/rules", response_model=list[TagRuleOut])
def tag_rules(conn: Conn) -> list[TagRuleOut]:
    return [TagRuleOut(**r.__dict__) for r in tags.rules(conn)]


@router.post("/tags/rules")
def add_tag_rule(body: TagRuleIn, conn: Conn) -> dict[str, int]:
    """Lines whose merchant text contains `pattern` get `tag`: those in the ledger now, and
    every one imported later."""
    pattern = body.pattern
    if body.txn_id is not None:
        row = conn.execute(
            text("SELECT description FROM transactions WHERE id = :t"), {"t": body.txn_id}
        ).first()
        if row is None:
            raise HTTPException(404, "no such transaction")
        pattern = cat.merchant_key(row.description)
    try:
        return {"tagged": tags.add_rule(conn, pattern, body.tag)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/tags/rules/{rule_id}")
def delete_tag_rule(rule_id: int, conn: Conn) -> dict[str, bool]:
    tags.delete_rule(conn, rule_id)
    return {"ok": True}


@router.put("/transactions/{txn_id}/tags", response_model=list[str])
def put_tags(txn_id: int, body: TagsIn, conn: Conn) -> list[str]:
    """Replace one transaction's tags."""
    return tags.set_tags(conn, txn_id, body.tags)


@router.post("/tags/apply")
def apply_tag(body: TagApplyIn, conn: Conn) -> dict[str, int]:
    """Put a tag on several transactions, or take it off them."""
    if body.remove:
        tags.remove(conn, body.ids, body.tag)
    else:
        tags.add(conn, body.ids, body.tag)
    return {"changed": len(body.ids)}


@router.get("/reconcile", response_model=list[Discrepancy])
def get_reconcile(conn: Conn, waiting: bool = False) -> list[Discrepancy]:
    """Balances the ledger cannot explain. `waiting=true` lists the other kind instead: fresh
    bank snapshots whose lines have not synced yet (they resolve on their own)."""
    from pf.ledger.reconcile import waiting_for_sync

    rows = waiting_for_sync(conn) if waiting else reconcile(conn)
    return [Discrepancy(**d.__dict__) for d in rows]


@router.get("/imports", response_model=list[ImportedFile])
def imports(conn: Conn) -> list[ImportedFile]:
    rows = conn.execute(
        text(
            "SELECT f.id, f.filename, f.importer, a.name AS account, f.period_start, f.period_end,"
            " f.n_rows, f.imported_at FROM import_files f"
            " LEFT JOIN accounts a ON a.id = f.account_id"
            " ORDER BY f.imported_at DESC, f.id DESC"
        )
    ).all()
    return [ImportedFile(**r._mapping) for r in rows]


@router.post("/import", response_model=list[ImportResult])
def upload(
    files: Annotated[list[UploadFile], File()],
    account: Annotated[str | None, Form()] = None,
) -> list[ImportResult]:
    """Each file is imported in its own DB transaction (one bad file doesn't block others)."""
    from pf.ledger.importing import import_file

    results: list[ImportResult] = []
    with tempfile.TemporaryDirectory() as tmp:
        for uf in files:
            dest = Path(tmp) / Path(uf.filename or "upload").name
            with dest.open("wb") as out:
                shutil.copyfileobj(uf.file, out)
            try:
                with make_engine().begin() as conn:
                    r = import_file(conn, dest, account=account or None)
                results.append(
                    ImportResult(
                        pending_sha=r.pending_sha,
                        suggested_account=r.suggested_account,
                        preview=r.preview,
                        message=r.message,
                        file=r.file,
                        status=r.status,
                        importer=r.importer,
                        account=r.account,
                        period=r.period,
                        new=r.new,
                        duplicates=r.duplicates,
                        opening_balance=r.opening_balance,
                        discrepancies=[Discrepancy(**d.__dict__) for d in r.discrepancies],
                    )
                )
            except (ImportError_, ValueError) as e:
                results.append(ImportResult(file=dest.name, status="error", message=str(e)))
    return results


@router.get("/rules", response_model=list[RuleOut])
def list_rules(conn: Conn) -> list[RuleOut]:
    rows = conn.execute(
        text(
            "SELECT id, pattern, target_account, match_type, field, payee, account_filter,"
            " priority, source FROM rules ORDER BY source, priority, id"
        )
    ).all()
    return [RuleOut(**r._mapping) for r in rows]


@router.post("/rules", response_model=RuleOut)
def add_rule(body: RuleIn, conn: Conn) -> RuleOut:
    rid = conn.execute(
        text(
            "INSERT INTO rules(pattern, target_account, match_type, field, payee, account_filter,"
            " priority, source) VALUES (:pattern, :target_account, :match_type, :field, :payee,"
            " :account_filter, :priority, 'user')"
        ),
        body.model_dump(),
    ).lastrowid
    cat.categorize(conn)
    return RuleOut(id=int(rid), source="user", **body.model_dump())  # type: ignore[arg-type]


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, conn: Conn) -> dict[str, bool]:
    conn.execute(text("DELETE FROM rules WHERE id = :i"), {"i": rule_id})
    return {"ok": True}


# ---------------------------------------------------------------- names that are you
class OwnerName(BaseModel):
    name: str
    source: Literal["user", "statement"] = "user"


@router.get("/owner-names", response_model=list[OwnerName])
def list_owner_names(conn: Conn) -> list[OwnerName]:
    rows = conn.execute(text("SELECT name, source FROM owner_names ORDER BY source, name")).all()
    return [OwnerName(name=r.name, source=r.source) for r in rows]


@router.post("/owner-names", response_model=list[OwnerName])
def add_owner_name(body: OwnerName, conn: Conn) -> list[OwnerName]:
    """A name of yours (e.g. your other Alipay login's real name): transfers to it are moves."""
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "empty name")
    conn.execute(
        text(
            "INSERT INTO owner_names(name, source) VALUES (:n, 'user')"
            " ON CONFLICT(name) DO UPDATE SET source = 'user'"
        ),
        {"n": name},
    )
    match_own_flows(conn)
    return list_owner_names(conn)


@router.delete("/owner-names", response_model=list[OwnerName])
def delete_owner_name(name: str, conn: Conn) -> list[OwnerName]:
    conn.execute(text("DELETE FROM owner_names WHERE name = :n"), {"n": name})
    return list_owner_names(conn)


# ---------------------------------------------------------------- changing the ledger by hand
def _one(conn: Connection, txn_id: int) -> Txn:
    row = conn.execute(text(f"{TXN_SQL} WHERE id = :t"), {"t": txn_id}).first()
    if row is None:
        raise HTTPException(404, "no such transaction")
    t = _txn(row)
    _with_parts(conn, [t])
    return t


def _money(s: str | None) -> Decimal | None:
    if s is None or not s.strip():
        return None
    try:
        return Decimal(s.strip().replace(",", ""))
    except InvalidOperation as e:
        raise HTTPException(422, f"not an amount: {s!r}") from e


def _day(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError as e:
        raise HTTPException(422, f"not a date: {s!r}") from e


@router.post("/transactions", response_model=Txn)
def add_transaction(body: ManualIn, conn: Conn) -> Txn:
    """Money that moved without a statement (cash, a friend paid you back, ...)."""
    amount, day = _money(body.amount), _day(body.date)
    if amount is None or amount == 0 or day is None:
        raise HTTPException(422, "date and a non-zero amount are needed")
    try:
        tid = edits.add_manual(
            conn,
            date=day,
            account=body.account,
            amount=amount,
            category=body.category,
            description=body.description.strip() or body.category.split(":")[-1],
            payee=body.payee,
            note=body.note,
        )
    except LedgerError as e:
        raise HTTPException(400, str(e)) from e
    return _one(conn, tid)


@router.patch("/transactions/{txn_id}", response_model=Txn)
def edit_transaction(txn_id: int, body: EditIn, conn: Conn) -> Txn:
    try:
        tid = edits.edit_txn(
            conn,
            txn_id,
            date=_day(body.date),
            amount=_money(body.amount),
            description=body.description,
            payee=body.payee,
            note=body.note,
        )
    except LedgerError as e:
        raise HTTPException(400, str(e)) from e
    return _one(conn, tid)


@router.post("/transactions/{txn_id}/split", response_model=Txn)
def split_transaction(txn_id: int, body: SplitIn, conn: Conn) -> Txn:
    parts = [(p.account, _money(p.amount) or Decimal(0)) for p in body.parts]
    try:
        tid = edits.split_txn(conn, txn_id, parts)
    except LedgerError as e:
        raise HTTPException(400, str(e)) from e
    return _one(conn, tid)


@router.delete("/transactions/{txn_id}")
def delete_transaction(txn_id: int, conn: Conn) -> dict[str, bool]:
    try:
        edits.delete_txn_by_user(conn, txn_id)
    except LedgerError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}
