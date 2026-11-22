from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from pf.importers.preview import StatementPreview

ReportCurrency = Literal["USD", "CNY"]


class Account(BaseModel):
    name: str
    type: str
    institution: str | None
    asset_class: str | None
    currency: str | None
    balance: str  # decimal string, ledger sign
    commodity: str
    flow_only: bool = False  # wallet: income/spending count, balance is not money held


class TxnPart(BaseModel):
    account: str
    amount: str


class Txn(BaseModel):
    id: int
    date: str
    description: str
    payee: str | None
    account: str  # statement account (for transfers: the paying account)
    counter_account: str | None  # category, or receiving account for transfers
    amount: str  # decimal string, statement-account ledger sign
    commodity: str
    status: Literal["pending", "confirmed"]
    kind: str
    category_source: str | None
    note: str | None
    # A split line: each category and the amount booked to it (category-leg sign). Empty otherwise.
    parts: list[TxnPart] = []
    edited: bool = False  # you changed this imported line (see pf.ledger.edits)
    tags: list[str] = []  # see pf.ledger.tags
    base_rate: str | None = None  # USD per unit, using this month’s reporting rate
    display_currency: str = "USD"
    display_rate: str | None = None  # selected reporting currency per unit
    report_original_amounts: dict[str, str] | None = None
    report_display_amount: str | None = None


class ManualIn(BaseModel):
    date: str
    account: str  # paid from / into this account of yours
    amount: str  # ledger sign of `account`: negative = money out
    category: str
    description: str
    payee: str | None = None
    note: str | None = None


class EditIn(BaseModel):
    date: str | None = None
    amount: str | None = None
    description: str | None = None
    payee: str | None = None
    note: str | None = None


class SplitIn(BaseModel):
    parts: list[TxnPart]  # amounts as spent (positive); empty = undo the split


class TxnPage(BaseModel):
    items: list[Txn]
    total: int
    currency: str = "USD"
    display_total: str | None = None
    original_totals: dict[str, str] = {}
    missing_fx: list[str] = []


class CategorizeIn(BaseModel):
    category: str
    payee: str | None = None
    remember: Literal["override", "rule", "none"] = "override"
    pattern: str | None = None


class CategorizeOut(BaseModel):
    also_updated: int


class IdsIn(BaseModel):
    ids: list[int]


class Discrepancy(BaseModel):
    account: str
    date: str
    commodity: str
    statement: str
    ledger: str
    diff: str
    waiting: bool = False  # a fresh bank snapshot whose lines have not synced yet


class ImportResult(BaseModel):
    file: str
    status: str
    importer: str | None = None
    account: str | None = None
    period: tuple[str, str] | None = None
    new: int = 0
    duplicates: int = 0
    opening_balance: str | None = None
    discrepancies: list[Discrepancy] = []
    message: str | None = None
    pending_sha: str | None = None
    suggested_account: str | None = None
    preview: StatementPreview | None = None


class ImportedFile(BaseModel):
    id: int
    filename: str
    importer: str
    account: str | None
    period_start: str | None
    period_end: str | None
    n_rows: int
    imported_at: str


class RuleIn(BaseModel):
    pattern: str
    target_account: str
    match_type: Literal["contains", "regex", "exact"] = "contains"
    field: Literal["description", "payee"] = "description"
    payee: str | None = None
    account_filter: str | None = None
    priority: int = 100


class RuleOut(RuleIn):
    id: int
    source: str


class ReviewSummary(BaseModel):
    uncategorized: int
    suggested: int
    possible_transfers: int
