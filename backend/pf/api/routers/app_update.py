"""Keel's own version and updates (see pf.ops.update)."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pf.i18n import tr
from pf.ops import update

router = APIRouter(prefix="/api/app", tags=["app"])


class Change(BaseModel):
    commit: str
    subject: str
    date: str


class VersionOut(BaseModel):
    version: str | None
    channel: str  # release: updates from GitHub Releases; source: from the git clone
    commit: str | None
    branch: str | None
    built_at: str | None
    source_mode: bool
    status: dict[str, str]


class CheckOut(BaseModel):
    current: str | None
    latest: str | None
    behind: int
    changes: list[Change]
    can_apply: bool
    reason: str | None
    error: str | None
    checked_at: str


@router.get("/version", response_model=VersionOut)
def version() -> VersionOut:
    info = update.build_info()
    return VersionOut(
        version=info.version,
        channel=info.channel,
        commit=info.commit,
        branch=info.branch,
        built_at=info.built_at,
        source_mode=info.source_mode,
        status=update.status(),
    )


@router.post("/update/check", response_model=CheckOut)
def check() -> CheckOut:
    return CheckOut.model_validate(asdict(update.check()))


@router.post("/update/apply", response_model=dict[str, str])
def apply() -> dict[str, str]:
    try:
        return update.apply()
    except ValueError as exc:
        raise HTTPException(409, tr("现在不能更新：{reason}", reason=str(exc))) from exc
