from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import Connection

from pf.api.deps import get_conn
from pf.prefs import Prefs, load_prefs, save_prefs

router = APIRouter(prefix="/api", tags=["settings"])
Conn = Annotated[Connection, Depends(get_conn)]


@router.get("/settings", response_model=Prefs)
def get_settings(conn: Conn) -> Prefs:
    return load_prefs(conn)


@router.put("/settings", response_model=Prefs)
def put_settings(body: Prefs, conn: Conn) -> Prefs:
    from pf.ops.background import apply

    saved = save_prefs(conn, body)
    apply(saved.background_sync)  # the LaunchAgent follows the switch (no-op outside Keel.app)
    return saved


class BackupOut(BaseModel):
    dir: str
    last: str | None
    count: int
    icloud_available: bool


@router.get("/backup", response_model=BackupOut)
def get_backup(conn: Conn) -> BackupOut:
    from pf.ops.backup import chosen_dir, status

    return BackupOut(**asdict(status(chosen_dir(conn))))


@router.post("/backup", response_model=BackupOut)
def backup_now(conn: Conn) -> BackupOut:
    """Snapshot the ledger now (skipped when nothing changed since the last one)."""
    from pf.ops.backup import chosen_dir, run_backup, status

    dest = chosen_dir(conn)
    run_backup(dest)
    return BackupOut(**asdict(status(dest)))


# ---------------------------------------------------------------- planning assumptions
EDITABLE_CLASSES = ("cash", "term_deposit", "brokerage", "retirement", "crypto", "real_estate")


class AssumptionsIO(BaseModel):
    """What Settings › 规划假设 shows and saves: no YAML for anyone to edit by hand."""

    base_currency: Literal["USD", "CNY"]
    expected_return: dict[str, float]  # per asset class, e.g. 0.08
    withdrawal_rate: float  # lighthouse 3: e.g. 0.04
    idle_cash_months: float
    fi_asset_classes: list[str]

    @field_validator("expected_return")
    @classmethod
    def _returns(cls, v: dict[str, float]) -> dict[str, float]:
        bad = [k for k, r in v.items() if k not in EDITABLE_CLASSES or not -0.5 <= r <= 0.5]
        if bad:
            raise ValueError(f"expected_return out of range or unknown: {bad}")
        return v

    @field_validator("withdrawal_rate")
    @classmethod
    def _rate(cls, v: float) -> float:
        if not 0 < v <= 0.2:
            raise ValueError("withdrawal_rate must be between 0 and 0.2")
        return v

    @field_validator("idle_cash_months")
    @classmethod
    def _months(cls, v: float) -> float:
        if not 0 <= v <= 60:
            raise ValueError("idle_cash_months must be between 0 and 60")
        return v

    @field_validator("fi_asset_classes")
    @classmethod
    def _classes(cls, v: list[str]) -> list[str]:
        if not v or any(c not in EDITABLE_CLASSES for c in v):
            raise ValueError("fi_asset_classes must be a non-empty list of known classes")
        return v


class AssumptionsOut(BaseModel):
    current: AssumptionsIO
    defaults: AssumptionsIO
    currencies: list[str]


def _io(raw: dict[str, Any]) -> AssumptionsIO:
    return AssumptionsIO(
        base_currency=raw["base_currency"],
        expected_return={k: float(raw["expected_return"].get(k, 0.0)) for k in EDITABLE_CLASSES},
        withdrawal_rate=float(raw["withdrawal_rate"]),
        idle_cash_months=float(raw["idle_cash_months"]),
        fi_asset_classes=[c for c in raw["fi_asset_classes"] if c in EDITABLE_CLASSES],
    )


def _assumptions_out() -> AssumptionsOut:
    from pf.reports.common import DEFAULT_ASSUMPTIONS, SUPPORTED_CURRENCIES, raw_assumptions

    return AssumptionsOut(
        current=_io(raw_assumptions()),
        defaults=_io(DEFAULT_ASSUMPTIONS),
        currencies=list(SUPPORTED_CURRENCIES),
    )


@router.get("/assumptions", response_model=AssumptionsOut)
def get_assumptions() -> AssumptionsOut:
    return _assumptions_out()


@router.put("/assumptions", response_model=AssumptionsOut)
def put_assumptions(body: AssumptionsIO) -> AssumptionsOut:
    """Saved to config/assumptions.local.yaml, which the app owns; untouched keys keep defaults."""
    import yaml

    from pf.reports.common import assumptions_local_path

    path = assumptions_local_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = {**(existing or {}), **body.model_dump()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "# Written by Keel (设置 › 规划假设). Change these in the app.\n"
        + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(path)
    return _assumptions_out()


@router.delete("/assumptions", response_model=AssumptionsOut)
def reset_assumptions() -> AssumptionsOut:
    """Back to the defaults: forget what Settings saved."""
    from pf.reports.common import assumptions_local_path

    assumptions_local_path().unlink(missing_ok=True)
    return _assumptions_out()
