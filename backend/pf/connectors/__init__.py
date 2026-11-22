"""API connectors (see base.py). One instance per source; `sync_all` runs every connected one."""

from __future__ import annotations

import threading

from sqlalchemy import Connection

from pf.connectors.base import Connector, Secrets, SyncResult, run_sync
from pf.connectors.ibkr_flex import IbkrFlex
from pf.connectors.mailbox import Mailbox
from pf.connectors.okx import Okx
from pf.connectors.plaid import Plaid
from pf.connectors.simplefin import SimpleFin
from pf.connectors.snaptrade import SnapTrade

CONNECTORS: dict[str, Connector] = {
    c.source: c for c in (Plaid(), SimpleFin(), SnapTrade(), IbkrFlex(), Okx(), Mailbox())
}


def connected() -> list[str]:
    return [s for s in CONNECTORS if Secrets.get(s)]


def sync_all(conn: Connection) -> list[SyncResult]:
    return [run_sync(conn, CONNECTORS[s]) for s in connected()]


# One sync at a time in this process: the daily job and a click on 立即同步 used to collide
# ("database is locked"). The second one now waits for the first.
_SYNCING = threading.Lock()


def sync_now(source: str | None = None) -> list[SyncResult]:
    """Sync one source, or every connected one. Each source commits by itself, so the ledger is
    never held locked while the next bank is being fetched over the network."""
    from pf.db import transaction

    out: list[SyncResult] = []
    with _SYNCING:
        for s in [source] if source else connected():
            with transaction() as conn:
                out.append(run_sync(conn, CONNECTORS[s]))
    return out
