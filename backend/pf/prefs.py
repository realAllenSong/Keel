"""App preferences kept in the ledger (app_settings 'prefs'): the web client mirrors language and
appearance here so the daily job and notifications match, plus the alert settings."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_validator
from sqlalchemy import Connection, text


class Prefs(BaseModel):
    language: Literal["system", "zh", "en"] = "system"
    theme: Literal["system", "light", "dark"] = "system"
    alerts: bool = True  # macOS notifications for new alerts (daily job)
    monthly_budget: str | None = None  # spending budget; None = your typical month
    low_balance: str = "500"  # warn when cash accounts are projected below this (USD)
    backup_icloud: bool = (
        False  # nightly snapshots go to iCloud Drive (another place than this disk)
    )
    auto_close: bool = True  # close a finished month by itself once it is complete
    background_sync: bool = True  # daily sync at 7:30 even when Keel is closed (pf.ops.background)

    @field_validator("monthly_budget", "low_balance")
    @classmethod
    def _decimal(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        d = Decimal(v)
        if d < 0 or not d.is_finite():
            raise ValueError("must be a non-negative number")
        return str(d)


def use_saved_language(conn: Connection) -> None:
    """Outside a request (jobs, notifications): write text in the language chosen in Settings."""
    from pf.i18n import LANG, resolve

    LANG.set(resolve(load_prefs(conn).language))


def load_prefs(conn: Connection) -> Prefs:
    body = conn.execute(
        text("SELECT value FROM app_settings WHERE key='prefs'")
    ).scalar_one_or_none()
    return Prefs.model_validate_json(body) if body else Prefs()


def save_prefs(conn: Connection, prefs: Prefs) -> Prefs:
    conn.execute(
        text("""INSERT INTO app_settings(key, value) VALUES ('prefs', :v)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value"""),
        {"v": prefs.model_dump_json()},
    )
    return prefs
