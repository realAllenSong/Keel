from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Connection

from pf.db import make_engine


def get_conn() -> Iterator[Connection]:
    """One request = one atomic DB transaction."""
    with make_engine().begin() as conn:
        yield conn
