"""Pending lines and money not yet posted: shown beside the books, never counted in them."""

import datetime as dt

import httpx
from sqlalchemy import Connection, text

from pf.connectors.base import Secrets, run_sync
from pf.connectors.plaid import Plaid
from pf.ledger.in_flight import in_flight
from tests.test_connectors import NOW, mock, plaid_handler


def _sync(conn: Connection) -> None:
    Secrets.set(
        "plaid",
        {
            "client_id": "cid",
            "secret": "sec",
            "items": [{"item_id": "I1", "access_token": "a", "institution": "Chase"}],
        },
    )
    assert run_sync(conn, Plaid(), client_factory=mock(plaid_handler()), now=NOW).ok


def test_pending_lines_are_shown_and_never_booked(conn: Connection) -> None:
    _sync(conn)
    booked = conn.execute(
        text("SELECT COUNT(*) FROM transactions WHERE description LIKE '%NETFLIX%'")
    ).scalar()
    assert booked == 0
    f = in_flight(conn, "USD", today=NOW.date())
    by_what = {x.description: x for x in f.lines}
    assert set(by_what) == {"NETFLIX", "MARRIOTT HOLD"}
    assert by_what["NETFLIX"].amount == "-9.99" and by_what["NETFLIX"].kind == "spend"
    assert by_what["MARRIOTT HOLD"].hold and not by_what["NETFLIX"].hold
    assert f.spend == "209.99" and f.spend_count == 2 and f.holds == 1
    # Checking: the bank already counts $1,000 more as available than it has posted.
    assert [(a.difference, a.current) for a in f.accounts] == [("1000", "460")]
    assert f.incoming == "1000.00"


def test_next_sync_replaces_the_pending_list(conn: Connection) -> None:
    _sync(conn)
    assert conn.execute(text("SELECT COUNT(*) FROM bank_pending")).scalar() == 2
    base = plaid_handler()

    def nothing_pending(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/transactions/get":
            return httpx.Response(200, json={"transactions": [], "total_transactions": 0})
        return base(req)

    later = NOW + dt.timedelta(hours=6)
    assert run_sync(conn, Plaid(), client_factory=mock(nothing_pending), now=later).ok
    f = in_flight(conn, "USD", today=later.date())
    assert f.lines == [] and f.spend == "0.00"  # posted or released: gone, books untouched


def test_a_failed_pending_lookup_keeps_the_last_picture(conn: Connection) -> None:
    _sync(conn)
    base = plaid_handler()

    def broken(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/transactions/get":
            return httpx.Response(400, json={"error_code": "PRODUCT_NOT_READY"})
        return base(req)

    later = NOW + dt.timedelta(hours=6)
    assert run_sync(conn, Plaid(), client_factory=mock(broken), now=later).ok
    assert len(in_flight(conn, "USD", today=later.date()).lines) == 2


def test_stale_reports_are_not_shown(conn: Connection) -> None:
    _sync(conn)
    later = NOW.date() + dt.timedelta(days=30)
    f = in_flight(conn, "USD", today=later)
    assert f.lines == [] and f.accounts == []
