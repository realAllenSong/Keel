from __future__ import annotations

import json
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import Connection, text

from pf.api.deps import get_conn
from pf.api.schemas import Discrepancy, ImportResult
from pf.i18n import tr
from pf.importers.archive_passwords import PasswordProvider
from pf.ledger import onboarding as service
from pf.ledger.balances import assert_balance
from pf.ledger.importing import suggested_name
from pf.onboarding_models import (
    BalanceIn,
    ChecklistItem,
    ClaimIn,
    Institution,
    OkResult,
    OnboardingState,
    PendingImport,
    TeachIn,
    UnlockIn,
)
from pf.ops.inbox import scan_watch_dirs

router = APIRouter(prefix="/api", tags=["onboarding"])
Conn = Annotated[Connection, Depends(get_conn)]


class BalanceResult(BaseModel):
    account: str
    discrepancies: list[Discrepancy]


class ScanOutcome(BaseModel):
    file: str
    path: str
    status: Literal[
        "imported",
        "already_imported",
        "needs_account",
        "unknown_format",
        "needs_password",
        "ignored",
        "dismissed",
        "error",
    ]
    message: str | None = None


class ScanResult(BaseModel):
    imported: list[ImportResult] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    failed: list[tuple[str, str]] = Field(default_factory=list)
    outcomes: list[ScanOutcome] = Field(default_factory=list)


@router.get("/onboarding/institutions", response_model=list[Institution])
def institutions() -> list[Institution]:
    return service.institutions()


@router.get("/onboarding/state", response_model=OnboardingState)
def state(conn: Conn) -> OnboardingState:
    body = conn.execute(
        text("SELECT value FROM app_settings WHERE key='onboarding'")
    ).scalar_one_or_none()
    return OnboardingState.model_validate_json(body) if body else OnboardingState()


@router.put("/onboarding/state", response_model=OnboardingState)
def set_state(body: OnboardingState, conn: Conn) -> OnboardingState:
    known = {i.id for i in service.institutions()}
    if set(body.selected_institutions) - known:
        raise HTTPException(400, tr("所选机构不存在"))
    conn.execute(
        text("""INSERT INTO app_settings(key, value) VALUES ('onboarding', :v)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value"""),
        {"v": body.model_dump_json()},
    )
    return body


@router.get("/onboarding/pending", response_model=list[PendingImport])
def pending(conn: Conn) -> list[PendingImport]:
    rows = (
        conn.execute(text("SELECT * FROM pending_imports ORDER BY created_at, sha256"))
        .mappings()
        .all()
    )
    items = []
    for row in rows:
        item = dict(row)
        item.pop("stored_path")
        item["preview"] = json.loads(item["preview"]) if item["preview"] else None
        asset_class = item.pop("asset_class", None)
        if item["reason"] == "needs_account":
            item["suggested_account"] = suggested_name(
                item["account_type"], item["institution"], asset_class, item["hint"]
            )
        items.append(PendingImport.model_validate(item))
    return items


@router.post("/onboarding/pending/{sha}/claim", response_model=ImportResult)
def claim(sha: str, body: ClaimIn, conn: Conn) -> ImportResult:
    try:
        return ImportResult.model_validate(
            asdict(service.claim_pending(conn, sha, body.account_name, body.remember))
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/onboarding/pending/{sha}/unlock", response_model=ImportResult)
def unlock(sha: str, body: UnlockIn, conn: Conn) -> ImportResult:
    try:
        return ImportResult.model_validate(
            asdict(service.unlock_pending(conn, sha, _password(body.password), body.remember))
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/onboarding/pending/{sha}/teach", response_model=ImportResult)
def teach(sha: str, body: TeachIn, conn: Conn) -> ImportResult:
    try:
        return ImportResult.model_validate(asdict(service.teach_profile(conn, sha, body.spec)))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/onboarding/pending/{sha}", response_model=OkResult)
def dismiss(sha: str, conn: Conn) -> OkResult:
    try:
        service.dismiss_pending(conn, sha)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return OkResult()


@router.get("/onboarding/checklist", response_model=list[ChecklistItem])
def checklist(
    conn: Conn, month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
) -> list[ChecklistItem]:
    try:
        return service.checklist(conn, month)
    except ValueError as exc:
        raise HTTPException(422, tr("月份格式应为 YYYY-MM")) from exc


@router.post("/accounts/balance", response_model=BalanceResult)
def balance(body: BalanceIn, conn: Conn) -> BalanceResult:
    try:
        issues = assert_balance(conn, body.account, body.date, body.amount, body.commodity)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return BalanceResult(
        account=body.account, discrepancies=[Discrepancy(**asdict(d)) for d in issues]
    )


@router.post("/inbox/scan", response_model=ScanResult)
def scan() -> ScanResult:
    return ScanResult.model_validate(asdict(scan_watch_dirs()))


class StatementPasswordIn(BaseModel):
    password: SecretStr


def _password(value: SecretStr) -> str:
    password = value.get_secret_value().strip()
    if not 1 <= len(password) <= 128:
        raise ValueError(tr("解压密码应为 1 至 128 个字符"))
    return password


@router.get("/onboarding/statement-passwords")
def statement_password_status() -> list[dict]:
    from pf.importers.archive_passwords import ArchivePasswords

    return ArchivePasswords.status()


@router.put("/onboarding/statement-passwords/{provider}")
def save_statement_password(
    provider: PasswordProvider, body: StatementPasswordIn, conn: Conn
) -> dict:
    from pf.importers.archive_passwords import ArchivePasswords

    try:
        ArchivePasswords.remember(provider, _password(body.password))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        **service.retry_saved_archive_passwords(conn, provider),
        "passwords": ArchivePasswords.status(),
    }


@router.delete("/onboarding/statement-passwords/{provider}")
def delete_statement_password(provider: PasswordProvider) -> list[dict]:
    from pf.importers.archive_passwords import ArchivePasswords

    ArchivePasswords.clear(provider)
    return ArchivePasswords.status()
