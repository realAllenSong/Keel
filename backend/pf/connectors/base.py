"""API connectors: fetch → one JSON statement per account under raw/connectors → import_file.

Each connector only knows its API. Everything else — dedup, cut-over from statement files,
transfer pairing, categorisation, reconciliation, rebuild — is the ordinary import pipeline
(see pf/importers/connector_json.py for the statement format).

Secrets (tokens, keys) live in the macOS Keychain under the service "Keel" (PF_SECRETS_FILE
points them at a JSON file instead, for tests and headless use). Non-secret state — when it
last synced, what happened, sync cursors — is kept in the ledger's app_settings.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy import Connection, text

from pf.settings import get_settings

log = logging.getLogger(__name__)
SERVICE = "Keel"
STATE_KEY = "connections"


class ConnectorError(RuntimeError):
    """A sync failed; `reauth` means the user has to sign in or paste a new key."""

    def __init__(self, message: str, *, reauth: bool = False) -> None:
        super().__init__(message)
        self.reauth = reauth


# ---------------------------------------------------------------- secrets
class Secrets:
    @staticmethod
    def _file() -> Path | None:
        p = os.environ.get("PF_SECRETS_FILE")
        return Path(p) if p else None

    @classmethod
    def get(cls, source: str) -> dict[str, Any] | None:
        if f := cls._file():
            data = json.loads(f.read_text()) if f.exists() else {}
            return data.get(source)
        import keyring

        raw = keyring.get_password(SERVICE, source)
        return json.loads(raw) if raw else None

    @classmethod
    def set(cls, source: str, values: dict[str, Any]) -> None:
        if f := cls._file():
            data = json.loads(f.read_text()) if f.exists() else {}
            data[source] = values
            f.write_text(json.dumps(data))
            f.chmod(0o600)
            return
        import keyring

        keyring.set_password(SERVICE, source, json.dumps(values))

    @classmethod
    def delete(cls, source: str) -> None:
        if f := cls._file():
            data = json.loads(f.read_text()) if f.exists() else {}
            data.pop(source, None)
            f.write_text(json.dumps(data))
            return
        import keyring
        from keyring.errors import PasswordDeleteError

        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(SERVICE, source)


# ---------------------------------------------------------------- state
def load_state(conn: Connection) -> dict[str, dict[str, Any]]:
    raw = conn.execute(
        text("SELECT value FROM app_settings WHERE key = :k"), {"k": STATE_KEY}
    ).scalar_one_or_none()
    return json.loads(raw) if raw else {}


def save_state(conn: Connection, state: dict[str, dict[str, Any]]) -> None:
    conn.execute(
        text(
            "INSERT INTO app_settings(key, value) VALUES (:k, :v)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        ),
        {"k": STATE_KEY, "v": json.dumps(state)},
    )


# ---------------------------------------------------------------- statements
@dataclass
class AccountStatement:
    """What one connector reports for one account in one sync (ledger signs throughout)."""

    source: str
    account_id: str
    institution: str
    type: str  # Assets | Liabilities
    currency: str = "USD"
    name: str | None = None
    mask: str | None = None
    asset_class: str | None = None
    balances: list[dict[str, str]] = field(default_factory=list)
    valuations: list[dict[str, str]] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    holdings: list[dict[str, Any]] = field(default_factory=list)
    # False when positions could not be read this time: an empty list then says nothing.
    holdings_complete: bool = True
    holdings_as_of: str | None = None  # the day the positions are for
    # Buys, sells, reinvestments, splits, option events (see pf/ledger/portfolio.py).
    trades: list[dict[str, Any]] = field(default_factory=list)

    def doc(self, fetched_at: str) -> dict[str, Any]:
        return {"keel_connector": 1, "fetched_at": fetched_at, **self.__dict__}


@dataclass
class Field:
    name: str
    label: str  # shown in Settings as the provider names it
    secret: bool = True


@dataclass
class FetchResult:
    """Usable statements can arrive while another connected institution is still pending."""

    statements: list[AccountStatement]
    warnings: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


class Connector(Protocol):
    source: str
    label: str
    fields: tuple[Field, ...]

    def fetch(
        self, client: httpx.Client, secrets: dict[str, Any], state: dict[str, Any]
    ) -> list[AccountStatement] | list[Path] | FetchResult:
        """Pull what changed since `state` (mutate it to remember cursors): statements, or
        downloaded statement files (the mailbox) that are imported like a dropped file."""
        ...


def write_statements(
    source: str, statements: list[AccountStatement], now: dt.datetime
) -> list[Path]:
    day = now.date().isoformat()
    out_dir = get_settings().raw_dir / "connectors" / source / day
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for st in statements:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", st.account_id)[:60] or "account"
        p = out_dir / f"{now:%H%M%S}-{slug}.json"
        p.write_text(
            json.dumps(st.doc(now.isoformat(timespec="seconds")), ensure_ascii=False, indent=1)
        )
        paths.append(p)
    return paths


@dataclass
class SyncResult:
    source: str
    ok: bool
    accounts: int = 0
    new: int = 0
    message: str | None = None
    reauth: bool = False
    partial: bool = False


def run_sync(
    conn: Connection,
    connector: Connector,
    *,
    client_factory: Callable[[], httpx.Client] | None = None,
    now: dt.datetime | None = None,
) -> SyncResult:
    """Fetch, write, import. A failure is recorded on the connection, never half-imported:
    each statement file is its own atomic import."""
    from pf.ledger.importing import import_file

    now = now or dt.datetime.now(dt.UTC)
    all_state = load_state(conn)
    state = all_state.setdefault(connector.source, {})
    secrets = Secrets.get(connector.source)
    if not secrets:
        return SyncResult(connector.source, False, message="not connected")
    factory = client_factory or (
        lambda: httpx.Client(timeout=60, headers={"User-Agent": "Keel/1.0"})
    )
    result = SyncResult(connector.source, True)
    # Cursors advance only once everything they cover is in the ledger.
    cursor = copy.deepcopy(state.get("cursor", {}))
    notes: list[str] = []
    try:
        with factory() as client:
            fetched = connector.fetch(client, secrets, cursor)
        if isinstance(fetched, FetchResult):
            notes.extend(dict.fromkeys(fetched.warnings))
            result.partial = bool(fetched.warnings)
            fetched = [*fetched.statements, *fetched.files]
        statements = [x for x in fetched if isinstance(x, AccountStatement)]
        files = [x for x in fetched if isinstance(x, Path)]
        for path in [*write_statements(connector.source, statements, now), *files]:
            with conn.begin_nested():  # a refused file leaves nothing behind
                report = import_file(conn, path)
            result.new += report.new
            if report.message and report.message not in notes:
                notes.append(report.message)
        result.accounts = len(statements) or len(files)
        result.message = "\n".join(notes) or None
        state.update(
            status="partial" if result.partial else "ok",
            message=result.message,
            last_sync=now.isoformat(timespec="seconds"),
            cursor=cursor,
        )
    except ConnectorError as exc:
        result = SyncResult(connector.source, False, message=str(exc), reauth=exc.reauth)
        state.update(status="reauth" if exc.reauth else "error", message=str(exc))
    except httpx.HTTPError as exc:
        result = SyncResult(connector.source, False, message=f"network: {exc}")
        state.update(status="error", message=result.message)
    except ValueError as exc:  # a statement the ledger refused (ImportError_ and friends)
        log.exception("%s import failed", connector.source)
        result = SyncResult(connector.source, False, message=str(exc))
        state.update(status="error", message=result.message)
    state["last_attempt"] = now.isoformat(timespec="seconds")
    state["accounts"] = result.accounts if result.ok else state.get("accounts", 0)
    save_state(conn, all_state)
    return result
