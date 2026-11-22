"""Shared onboarding contracts keep persisted decisions and OpenAPI in sync."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from pf.i18n import tr
from pf.importers.preview import StatementPreview


class Columns(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    description: str
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    balance: str | None = None
    payee: str | None = None
    ref: str | None = None

    @model_validator(mode="after")
    def amount_mapping(self) -> Self:
        if bool(self.amount) == bool(self.debit or self.credit) or (
            not self.amount and not (self.debit and self.credit)
        ):
            raise ValueError(tr("请映射金额列，或同时映射支出和收入列"))
        values = [v for v in self.model_dump().values() if v is not None]
        if any(not v.strip() for v in values) or len(set(values)) != len(values):
            raise ValueError(tr("列映射不能为空或重复"))
        return self


class ProfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    institution: str = Field(min_length=1, max_length=100)
    account_type: Literal["Assets", "Liabilities"]
    asset_class: (
        Literal["cash", "brokerage", "retirement", "crypto", "real_estate", "vehicle", "other"]
        | None
    ) = "cash"
    currency: str = Field(min_length=1, max_length=20)
    columns: Columns
    date_format: str = Field(min_length=1)
    sign: Literal["as_is", "negate"]
    header: list[str] = Field(min_length=3)


class Institution(BaseModel):
    id: str
    name: str
    kind: Literal["bank", "card", "broker", "payment", "crypto"]
    importers: list[str]
    export_url: str
    steps: list[str]
    supported: bool
    note: str
    steps_verified: bool = False


class OnboardingState(BaseModel):
    selected_institutions: list[str] = Field(default_factory=list)
    completed: bool = False
    step: int = Field(default=1, ge=1, le=6)


class PendingImport(BaseModel):
    sha256: str
    filename: str
    reason: Literal["needs_account", "unknown_format", "needs_password"]
    importer: str | None
    institution: str | None
    account_type: str | None
    hint: str | None
    preview: StatementPreview | None
    created_at: str
    suggested_account: str | None = None


class UnlockIn(BaseModel):
    password: SecretStr
    remember: bool = False


class ClaimIn(BaseModel):
    account_name: str = Field(min_length=1)
    remember: bool = True


class TeachIn(BaseModel):
    spec: ProfileSpec


class ChecklistItem(BaseModel):
    account: str
    institution: str | None
    last_transaction_date: str | None
    period_end: str | None
    status: Literal["ok", "stale", "missing"]
    export_url: str | None
    steps: list[str]


class BalanceIn(BaseModel):
    account: str
    date: dt.date
    amount: Decimal = Field(allow_inf_nan=False)
    commodity: str = "USD"


class OkResult(BaseModel):
    ok: bool = True
