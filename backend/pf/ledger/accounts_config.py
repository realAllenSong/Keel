"""Binds imported files to ledger accounts using config/accounts.local.yaml (private) or
config/accounts.example.yaml (synthetic, committed)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import Connection, text

from pf.importers.base import ParsedStatement
from pf.settings import get_settings


@dataclass(frozen=True)
class AccountSpec:
    name: str
    importer: str | None = None
    hint: str | None = None
    asset_class: str | None = None
    currency: str | None = None
    institution: str | None = None


def config_path() -> Path:
    d = get_settings().config_dir
    local = d / "accounts.local.yaml"
    return local if local.exists() else d / "accounts.example.yaml"


def load_specs(path: Path | None = None) -> list[AccountSpec]:
    path = path or config_path()
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs: list[AccountSpec] = []
    for a in data.get("accounts", []):
        opt = {k: str(v) for k, v in a.items() if k != "name" and v is not None}
        specs.append(AccountSpec(name=str(a["name"]), **opt))
    return specs


def resolve_account(
    stmt: ParsedStatement, specs: list[AccountSpec], conn: Connection | None = None
) -> str | None:
    if conn is not None:
        for hint in dict.fromkeys((stmt.account_hint or "", "")):
            name = conn.execute(
                text("SELECT account_name FROM account_bindings WHERE importer=:i AND hint=:h"),
                {"i": stmt.importer, "h": hint},
            ).scalar_one_or_none()
            if name:
                return str(name)
    cands = [s for s in specs if s.importer == stmt.importer]
    if stmt.account_hint:
        hinted = [s for s in cands if s.hint == stmt.account_hint]
        if hinted:
            cands = hinted
    unhinted = [s for s in cands if not s.hint or s.hint == stmt.account_hint]
    return unhinted[0].name if len(unhinted) == 1 else None
