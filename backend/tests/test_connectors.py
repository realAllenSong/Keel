"""API connectors against mocked HTTP in each API's documented shape (no network, no Keychain)."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from pf.connectors.base import Secrets, load_state, run_sync, save_state
from pf.connectors.ibkr_flex import IbkrFlex
from pf.connectors.okx import Okx
from pf.connectors.plaid import Plaid
from pf.connectors.simplefin import SimpleFin
from pf.connectors.snaptrade import SnapTrade, signature
from pf.ledger.importing import import_file
from pf.ledger.reconcile import reconcile

NOW = dt.datetime(2026, 10, 31, 8, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def secrets_file(env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    f = env / "secrets.json"
    monkeypatch.setenv("PF_SECRETS_FILE", str(f))
    return f


def mock(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[[], httpx.Client]:
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def lines(conn: Connection, account_like: str) -> list[tuple[str, int, str]]:
    return [
        tuple(r)
        for r in conn.execute(
            text(
                "SELECT t.date, p.amount, t.description FROM transactions t"
                " JOIN postings p ON p.txn_id = t.id JOIN accounts a ON a.id = p.account_id"
                " WHERE a.name LIKE :a AND t.kind IN ('normal', 'transfer')"  # not derived lines
                " ORDER BY t.date, p.amount"
            ),
            {"a": account_like},
        )
    ]


# ---------------------------------------------------------------- IBKR Flex
FLEX = """<FlexQueryResponse queryName="Keel" type="AF"><FlexStatements count="1">
<FlexStatement accountId="U0001234" fromDate="20251101" toDate="20261030">
 <AccountInformation accountId="U0001234" currency="USD" accountType="Individual"/>
 <EquitySummaryInBase>
  <EquitySummaryByReportDateInBase reportDate="20261029" total="1010.50"/>
  <EquitySummaryByReportDateInBase reportDate="20261030" total="1012.25"/>
 </EquitySummaryInBase>
 <CashTransactions>
  <CashTransaction currency="USD" fxRateToBase="1" dateTime="20260901;100000" amount="1000" type="Deposits/Withdrawals" description="ELECTRONIC FUND TRANSFER" transactionID="T1" levelOfDetail="DETAIL"/>
  <CashTransaction currency="USD" fxRateToBase="1" dateTime="20261008;202000" amount="0.14" type="Dividends" description="QQQ CASH DIVIDEND" transactionID="T2" levelOfDetail="DETAIL"/>
  <CashTransaction currency="USD" fxRateToBase="1" dateTime="20261008;202000" amount="-0.01" type="Withholding Tax" description="QQQ US TAX" transactionID="T3" levelOfDetail="DETAIL"/>
  <CashTransaction currency="USD" fxRateToBase="1" dateTime="20261008" amount="0.13" type="Dividends" description="summary" levelOfDetail="SUMMARY"/>
 </CashTransactions>
 <OpenPositions><OpenPosition symbol="QQQ" position="1.2" markPrice="520" positionValue="624" costBasisMoney="600" currency="USD" levelOfDetail="SUMMARY"/></OpenPositions>
</FlexStatement></FlexStatements></FlexQueryResponse>"""


def flex_handler(
    pending_first: bool = True, fail: str | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    calls = {"get": 0}

    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("SendRequest"):
            if fail:
                return httpx.Response(
                    200,
                    text=f"<FlexStatementResponse><Status>Fail</Status><ErrorCode>{fail}</ErrorCode><ErrorMessage>Token has expired.</ErrorMessage></FlexStatementResponse>",
                )
            assert req.url.params["t"] == "tok" and req.url.params["q"] == "99"
            return httpx.Response(
                200,
                text="<FlexStatementResponse><Status>Success</Status><ReferenceCode>R1</ReferenceCode><Url>https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url></FlexStatementResponse>",
            )
        calls["get"] += 1
        assert req.url.params["q"] == "R1"
        if pending_first and calls["get"] == 1:
            return httpx.Response(
                200,
                text="<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode><ErrorMessage>Statement generation in progress.</ErrorMessage></FlexStatementResponse>",
            )
        return httpx.Response(200, text=FLEX)

    return handle


def test_ibkr_flex_polls_then_books_flows_and_daily_value(conn: Connection) -> None:
    Secrets.set("ibkr_flex", {"token": "tok", "query_id": "99"})
    flex = IbkrFlex(sleep=lambda s: None)
    r = run_sync(conn, flex, client_factory=mock(flex_handler()), now=NOW)
    assert (r.ok, r.accounts, r.new) == (True, 1, 3)
    account = conn.execute(text("SELECT name FROM accounts WHERE institution='IBKR'")).scalar_one()
    assert account == "Assets:IBKR:Brokerage-1234"
    marks = conn.execute(text("SELECT date, value FROM valuations ORDER BY date")).all()
    assert [tuple(m) for m in marks] == [("2026-10-29", 101050), ("2026-10-30", 101225)]
    raw = sorted((Path(conn.execute(text("SELECT raw_path FROM import_files")).scalar_one())).parts)
    assert "connectors" in raw
    # Same statement again: nothing new.
    again = run_sync(conn, flex, client_factory=mock(flex_handler(pending_first=False)), now=NOW)
    assert (again.ok, again.new) == (True, 0)
    assert load_state(conn)["ibkr_flex"]["status"] == "ok"


def test_ibkr_flex_expired_token_asks_to_reconnect(conn: Connection) -> None:
    Secrets.set("ibkr_flex", {"token": "old", "query_id": "99"})
    r = run_sync(
        conn,
        IbkrFlex(sleep=lambda s: None),
        client_factory=mock(flex_handler(fail="1012")),
        now=NOW,
    )
    assert (r.ok, r.reauth) == (False, True)
    assert load_state(conn)["ibkr_flex"]["status"] == "reauth"


def test_ibkr_account_info_only_reports_missing_sections(conn: Connection) -> None:
    Secrets.set("ibkr_flex", {"token": "tok", "query_id": "99"})
    send = flex_handler(pending_first=False)

    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("SendRequest"):
            return send(req)
        return httpx.Response(
            200,
            text='<FlexQueryResponse><FlexStatements><FlexStatement accountId="U0001234"><AccountInformation currency="USD"/></FlexStatement></FlexStatements></FlexQueryResponse>',
        )

    result = run_sync(conn, IbkrFlex(), client_factory=mock(handle), now=NOW)
    assert not result.ok
    assert "NAV" in (result.message or "")
    assert load_state(conn)["ibkr_flex"]["status"] == "error"


@pytest.mark.parametrize("first,second", [("ibkr_flex", "snaptrade"), ("snaptrade", "ibkr_flex")])
def test_two_connectors_do_not_book_same_brokerage_twice(
    conn: Connection, env: Path, first: str, second: str
) -> None:
    from pf.ledger.rebuild import rebuild

    for i, source in enumerate((first, second)):
        path = env / f"{source}.json"
        path.write_text(
            json.dumps(
                {
                    "keel_connector": 1,
                    "source": source,
                    "account_id": "U0001234",
                    "institution": "IBKR" if source == "ibkr_flex" else "Interactive Brokers",
                    "type": "Assets",
                    "mask": "1234",
                    "asset_class": "brokerage",
                    "transactions": [
                        {
                            "id": f"different-provider-id-{i}",
                            "date": "2026-10-15",
                            "amount": "10",
                            "description": f"Provider {i} dividend",
                            "category": "Income:Dividends",
                        }
                    ],
                    "valuations": [{"date": "2026-10-30", "value": str(1010 + i)}],
                }
            )
        )
        result = import_file(conn, path)
        assert result.new == (1 if i == 0 else 0)
        if i == 1:
            assert first in (result.message or "")
    for _ in range(2):
        assert len(lines(conn, "Assets:IBKR:Brokerage-1234")) == 1
        assert (
            conn.execute(
                text("SELECT value FROM valuations WHERE account_name='Assets:IBKR:Brokerage-1234'")
            ).scalar_one()
            == 101000
        )
        rebuild(conn)


def test_connector_takes_over_after_statement_files(conn: Connection, env: Path) -> None:
    csv = env / "U0001234.TRANSACTIONS.csv"
    csv.write_text(
        "Statement,Header,Field Name,Field Value\n"
        "Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount,Commission,Net Amount\n"
        "Transaction History,Data,2026-09-01,U***1234,ELECTRONIC FUND TRANSFER,Deposit,-,-,-,-,1000,-,1000\n",
        encoding="utf-8",
    )
    import_file(conn, csv, account="Assets:IBKR:Brokerage")
    Secrets.set("ibkr_flex", {"token": "tok", "query_id": "99"})
    r = run_sync(
        conn,
        IbkrFlex(sleep=lambda s: None),
        client_factory=mock(flex_handler(pending_first=False)),
        now=NOW,
    )
    assert r.ok
    # Same account (one IBKR account), the file's 09-01 deposit is not booked a second time.
    assert (
        conn.execute(text("SELECT COUNT(*) FROM accounts WHERE institution='IBKR'")).scalar() == 1
    )
    got = lines(conn, "Assets:IBKR:Brokerage")
    assert [d for d, _, _ in got] == ["2026-09-01", "2026-10-08", "2026-10-08"]


# ---------------------------------------------------------------- Plaid
def plaid_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body["client_id"] == "cid" and body["secret"] == "sec"
        if req.url.path == "/transactions/sync":
            if body.get("cursor") is None:
                return httpx.Response(
                    200,
                    json={
                        "added": [
                            {
                                "transaction_id": "c1",
                                "account_id": "card",
                                "amount": 12.5,
                                "date": "2026-10-20",
                                "name": "SHAKE SHACK",
                                "merchant_name": "Shake Shack",
                                "pending": False,
                                "personal_finance_category": {
                                    "primary": "FOOD_AND_DRINK",
                                    "detailed": "FOOD_AND_DRINK_RESTAURANT",
                                },
                            },
                            {
                                "transaction_id": "c2",
                                "account_id": "card",
                                "amount": -40,
                                "date": "2026-10-22",
                                "name": "Payment Thank You",
                                "pending": False,
                            },
                            {
                                "transaction_id": "k1",
                                "account_id": "chk",
                                "amount": 40,
                                "date": "2026-10-22",
                                "name": "Payment to Chase card",
                                "pending": False,
                            },
                            {
                                "transaction_id": "k2",
                                "account_id": "chk",
                                "amount": 9.99,
                                "date": "2026-10-30",
                                "name": "NETFLIX",
                                "pending": True,
                            },
                        ],
                        "modified": [],
                        "removed": [],
                        "next_cursor": "C1",
                        "has_more": False,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "added": [],
                    "modified": [],
                    "removed": [],
                    "next_cursor": "C1",
                    "has_more": False,
                },
            )
        if req.url.path == "/transactions/get":
            return httpx.Response(
                200,
                json={
                    "transactions": [
                        {
                            "transaction_id": "k2",
                            "account_id": "chk",
                            "amount": 9.99,
                            "date": "2026-10-30",
                            "name": "NETFLIX",
                            "pending": True,
                            "personal_finance_category": {
                                "primary": "ENTERTAINMENT",
                                "detailed": "ENTERTAINMENT_TV_AND_MOVIES",
                            },
                        },
                        {
                            "transaction_id": "h1",
                            "account_id": "card",
                            "amount": 200,
                            "date": "2026-10-31",
                            "name": "MARRIOTT HOLD",
                            "merchant_name": "Marriott",
                            "pending": True,
                            "personal_finance_category": {
                                "primary": "TRAVEL",
                                "detailed": "TRAVEL_LODGING",
                            },
                        },
                        {
                            "transaction_id": "c1",
                            "account_id": "card",
                            "amount": 12.5,
                            "date": "2026-10-20",
                            "name": "SHAKE SHACK",
                            "pending": False,
                        },
                    ],
                    "total_transactions": 3,
                },
            )
        if req.url.path == "/accounts/get":
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {
                            "account_id": "card",
                            "name": "Sapphire",
                            "mask": "0002",
                            "type": "credit",
                            "balances": {"current": 112.5, "iso_currency_code": "USD"},
                        },
                        {
                            "account_id": "chk",
                            "name": "Total Checking",
                            "mask": "0102",
                            "type": "depository",
                            "balances": {
                                "current": 460,
                                "available": 1460,
                                "iso_currency_code": "USD",
                            },
                        },
                        {
                            "account_id": "inv",
                            "name": "Brokerage",
                            "mask": "1111",
                            "type": "investment",
                            "balances": {"current": 1},
                        },
                    ]
                },
            )
        raise AssertionError(req.url.path)

    return handle


def test_plaid_signs_cards_as_debt_and_skips_pending(conn: Connection) -> None:
    Secrets.set(
        "plaid",
        {
            "client_id": "cid",
            "secret": "sec",
            "items": [{"item_id": "I1", "access_token": "a", "institution": "Chase"}],
        },
    )
    r = run_sync(conn, Plaid(), client_factory=mock(plaid_handler()), now=NOW)
    assert (r.ok, r.accounts, r.new) == (True, 2, 3)  # pending Netflix waits; investment skipped
    card = lines(conn, "Liabilities:Chase:%")
    assert [(d, a) for d, a, _ in card] == [("2026-10-20", -1250), ("2026-10-22", 4000)]
    # The card's balance became an assertion (debt negative); checking has a pending line, so not.
    asserted = dict(
        conn.execute(
            text(
                "SELECT a.name, b.amount FROM balance_assertions b JOIN accounts a ON a.id = b.account_id"
            )
        ).all()
    )
    assert asserted == {"Liabilities:Chase:Card-0002": -11250}
    assert reconcile(conn, "Liabilities:Chase:Card-0002") == []
    # A snapshot pulled before a line posted (dated by when it happened) is not a discrepancy …
    acc, file_id = conn.execute(
        text("SELECT account_id, import_file_id FROM balance_assertions")
    ).one()
    held = conn.execute(
        text("SELECT SUM(amount) FROM v_postings WHERE account_id = :a AND date <= '2026-10-21'"),
        {"a": acc},
    ).scalar_one()
    snap = "INSERT INTO balance_assertions (account_id, date, amount, commodity, import_file_id)"
    conn.execute(
        text(snap + " VALUES (:a, '2026-10-23', :m, 'USD', :f)"),
        {"a": acc, "m": held, "f": file_id},
    )
    assert reconcile(conn, "Liabilities:Chase:Card-0002") == []
    # … but a balance the ledger never held nearby still is.
    conn.execute(
        text(snap + " VALUES (:a, '2026-10-24', :m, 'USD', :f)"),
        {"a": acc, "m": held + 1, "f": file_id},
    )
    assert [d.date for d in reconcile(conn, "Liabilities:Chase:Card-0002")] == ["2026-10-24"]
    conn.execute(text("DELETE FROM balance_assertions WHERE date IN ('2026-10-23', '2026-10-24')"))
    # The card payment pairs with the checking side as a transfer, not spending.
    kinds = {
        k
        for (k,) in conn.execute(
            text("SELECT DISTINCT kind FROM transactions WHERE description LIKE 'Payment%'")
        )
    }
    assert kinds == {"transfer"}
    # Plaid's own category suggests one.
    cat = conn.execute(
        text(
            "SELECT a.name FROM postings p JOIN accounts a ON a.id=p.account_id JOIN transactions t ON t.id=p.txn_id WHERE t.description='SHAKE SHACK' AND a.type='Expenses'"
        )
    ).scalar_one()
    assert cat == "Expenses:Food:Dining"
    assert load_state(conn)["plaid"]["cursor"]["I1"] == "C1"


def test_failed_import_does_not_advance_the_cursor(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    Secrets.set(
        "plaid",
        {
            "client_id": "cid",
            "secret": "sec",
            "items": [{"item_id": "I1", "access_token": "a", "institution": "Chase"}],
        },
    )

    def boom(*a: object, **k: object) -> None:
        raise ValueError("ledger refused")

    monkeypatch.setattr("pf.ledger.importing.import_file", boom)
    r = run_sync(conn, Plaid(), client_factory=mock(plaid_handler()), now=NOW)
    assert not r.ok and "refused" in (r.message or "")
    assert (
        "cursor" not in load_state(conn)["plaid"] or "I1" not in load_state(conn)["plaid"]["cursor"]
    )


# ---------------------------------------------------------------- SnapTrade
def test_snaptrade_signature_is_canonical_json_hmac() -> None:
    sig = signature(
        "key", "/api/v1/snapTrade/registerUser", "clientId=c&timestamp=1", {"userId": "u"}
    )
    payload = '{"content":{"userId":"u"},"path":"/api/v1/snapTrade/registerUser","query":"clientId=c&timestamp=1"}'
    assert (
        sig
        == base64.b64encode(hmac.new(b"key", payload.encode(), hashlib.sha256).digest()).decode()
    )
    assert signature("key", "/api/v1/accounts", "q", {}) == signature(
        "key", "/api/v1/accounts", "q", None
    )


def test_snaptrade_books_contributions_dividends_and_value(conn: Connection) -> None:
    Secrets.set(
        "snaptrade", {"client_id": "c", "consumer_key": "k", "user_id": "u", "user_secret": "s"}
    )

    def handle(req: httpx.Request) -> httpx.Response:
        assert "Signature" in req.headers and req.url.params["clientId"] == "c"
        p = req.url.path
        if p == "/api/v1/authorizations":
            return httpx.Response(200, json=[])
        if p == "/api/v1/accounts":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "A1",
                        "name": "Individual",
                        "number": "Z35654263",
                        "institution_name": "Fidelity",
                        "balance": {"total": {"amount": 6100.25, "currency": "USD"}},
                    }
                ],
            )
        if p == "/api/v1/accounts/A1/activities":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "x1",
                            "type": "CONTRIBUTION",
                            "amount": 500,
                            "currency": {"code": "USD"},
                            "settlement_date": "2026-10-01T00:00:00Z",
                            "description": "EFT",
                        },
                        {
                            "id": "x2",
                            "type": "DIVIDEND",
                            "amount": 3.21,
                            "currency": {"code": "USD"},
                            "settlement_date": "2026-10-15T00:00:00Z",
                            "description": "SPAXX",
                        },
                        {
                            "id": "x3",
                            "type": "BUY",
                            "amount": -400,
                            "currency": {"code": "USD"},
                            "settlement_date": "2026-10-02T00:00:00Z",
                            "description": "QQQ",
                        },
                    ],
                    "pagination": {"offset": 0, "limit": 1000, "total": 3},
                },
            )
        if p == "/api/v1/accounts/A1/positions/all":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "instrument": {"kind": "etf", "symbol": "QQQ"},
                            "units": "0.8",
                            "price": "520",
                            "cost_basis": "500",
                            "currency": "USD",
                        }
                    ]
                },
            )
        raise AssertionError(p)

    r = run_sync(conn, SnapTrade(), client_factory=mock(handle), now=NOW)
    assert (r.ok, r.new) == (True, 2)
    got = lines(conn, "Assets:Fidelity:%")
    assert [(d, a) for d, a, _ in got if a > 0] == [("2026-10-01", 50000), ("2026-10-15", 321)]
    assert conn.execute(text("SELECT value FROM valuations")).scalar_one() == 610025


# ---------------------------------------------------------------- OKX
@pytest.mark.parametrize("problem", ["missing", "initial", "disabled", "positions", "health"])
def test_snaptrade_reports_incomplete_broker_data_without_zeroing_nav(
    conn: Connection, problem: str
) -> None:
    Secrets.set("snaptrade", {"client_id": "PERS-ABC", "consumer_key": "k"})
    recovered = False

    def handle(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/v1/authorizations":
            if problem == "health" and not recovered:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json=[
                    {"id": "F", "brokerage": {"name": "Fidelity"}, "disabled": False},
                    {
                        "id": "I",
                        "brokerage": {"name": "Interactive Brokers"},
                        "disabled": problem == "disabled" and not recovered,
                    },
                ],
            )
        if p == "/api/v1/accounts":
            accounts = [
                {
                    "id": "A1",
                    "institution_name": "Fidelity",
                    "number": "X1234",
                    "brokerage_authorization": "F",
                    "balance": {"total": {"amount": 3908.73, "currency": "USD"}},
                }
            ]
            if problem != "missing" or recovered:
                accounts.append(
                    {
                        "id": "A2",
                        "institution_name": "Interactive Brokers",
                        "number": "X5678",
                        "brokerage_authorization": "I",
                        "balance": {
                            "total": {
                                "amount": 0 if problem == "initial" and not recovered else 1000.006,
                                "currency": "USD",
                            }
                        },
                        "sync_status": {
                            "holdings": {
                                "initial_sync_completed": problem != "initial" or recovered
                            }
                        },
                    }
                )
            return httpx.Response(200, json=accounts)
        if p.endswith("/activities"):
            return httpx.Response(200, json=[])
        if p.endswith("/positions/all"):
            return httpx.Response(
                503 if problem == "positions" and not recovered else 200, json={"results": []}
            )
        raise AssertionError(p)

    result = run_sync(conn, SnapTrade(), client_factory=mock(handle), now=NOW)
    assert result.ok and result.partial
    assert load_state(conn)["snaptrade"]["status"] == "partial"
    assert result.message
    if problem in {"missing", "initial", "disabled"}:
        assert "Interactive Brokers" in result.message
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM valuations WHERE account_name LIKE '%IBKR%'")
            ).scalar_one()
            == 0
        )
    assert (
        conn.execute(
            text("SELECT value FROM valuations WHERE account_name LIKE '%Fidelity%'")
        ).scalar_one()
        == 390873
    )
    recovered = True
    result = run_sync(conn, SnapTrade(), client_factory=mock(handle), now=NOW)
    assert result.ok and not result.partial and result.message is None
    assert result.accounts == 2
    assert load_state(conn)["snaptrade"]["status"] == "ok"
    assert (
        conn.execute(
            text("SELECT value FROM valuations WHERE account_name LIKE '%IBKR%'")
        ).scalar_one()
        == 100001
    )


def test_snaptrade_reads_current_positions_api_without_adding_cash_equivalents_to_nav(
    conn: Connection, env: Path
) -> None:
    Secrets.set("snaptrade", {"client_id": "PERS-ABC", "consumer_key": "k"})

    def handle(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/authorizations") or p.endswith("/activities"):
            return httpx.Response(200, json=[])
        if p == "/api/v1/accounts":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "A1",
                        "institution_name": "Fidelity",
                        "number": "X1234",
                        "balance": {"total": {"amount": 6100.25, "currency": "USD"}},
                    }
                ],
            )
        if p.endswith("/positions/all"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "instrument": {"kind": "etf", "symbol": "QQQ", "currency": "USD"},
                            "units": "0.8",
                            "price": "520",
                            "cost_basis": "500",
                            "currency": "USD",
                        },
                        {
                            "instrument": {"kind": "stock", "symbol": "SHOP", "currency": "CAD"},
                            "units": "2",
                            "price": "100",
                            "cost_basis": None,
                            "currency": "CAD",
                        },
                        {
                            "instrument": {"kind": "mutualfund", "symbol": "SPAXX"},
                            "units": "100",
                            "price": "1",
                            "cash_equivalent": True,
                        },
                    ],
                    "data_freshness": {"as_of": "2026-10-31T02:00:00Z"},
                },
            )
        if p.endswith("/positions"):
            return httpx.Response(
                410, json={"detail": "This endpoint is no longer available for your account."}
            )
        raise AssertionError(p)

    result = run_sync(conn, SnapTrade(), client_factory=mock(handle), now=NOW)
    assert result.ok and not result.partial
    raw = json.loads(
        (
            env / "raw" / conn.execute(text("SELECT raw_path FROM import_files")).scalar_one()
        ).read_text()
    )
    assert [h["symbol"] for h in raw["holdings"]] == ["QQQ", "SHOP", "SPAXX"]
    assert raw["holdings"][0]["cost_basis"] == "500"
    assert raw["holdings"][1]["currency"] == "CAD"
    assert raw["holdings"][1]["cost_basis"] is None
    assert raw["holdings"][2]["cash_equivalent"] is True
    assert conn.execute(text("SELECT value FROM valuations")).scalar_one() == 610025


def test_successful_empty_sync_clears_previous_account_count(conn: Connection) -> None:
    from pf.connectors.base import save_state

    Secrets.set("snaptrade", {"client_id": "PERS-ABC", "consumer_key": "k"})
    save_state(conn, {"snaptrade": {"accounts": 2, "status": "ok"}})
    result = run_sync(
        conn, SnapTrade(), client_factory=mock(lambda r: httpx.Response(200, json=[])), now=NOW
    )
    assert result.ok and result.partial and result.accounts == 0
    assert load_state(conn)["snaptrade"]["accounts"] == 0


# ---------------------------------------------------------------- OKX
def test_okx_values_the_exchange_and_books_stablecoin_moves(conn: Connection) -> None:
    Secrets.set("okx", {"api_key": "k", "secret_key": "s", "passphrase": "p"})
    ms = lambda d: str(int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.UTC).timestamp() * 1000))  # noqa: E731

    def handle(req: httpx.Request) -> httpx.Response:
        h = req.headers
        prehash = f"{h['OK-ACCESS-TIMESTAMP']}GET{req.url.raw_path.decode()}"
        assert (
            h["OK-ACCESS-SIGN"]
            == base64.b64encode(hmac.new(b"s", prehash.encode(), hashlib.sha256).digest()).decode()
        )
        p = req.url.path
        if p.endswith("asset-valuation"):
            return httpx.Response(200, json={"code": "0", "data": [{"totalBal": "250.75"}]})
        if p.endswith("deposit-history"):
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {
                            "ccy": "USDT",
                            "amt": "200",
                            "ts": ms("2026-10-10T00:00:00"),
                            "depId": "d1",
                            "state": "2",
                        },
                        {
                            "ccy": "BTC",
                            "amt": "0.01",
                            "ts": ms("2026-10-11T00:00:00"),
                            "depId": "d2",
                            "state": "2",
                        },
                    ],
                },
            )
        if p.endswith("withdrawal-history"):
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {
                            "ccy": "USDC",
                            "amt": "50",
                            "fee": "1",
                            "ts": ms("2026-10-20T00:00:00"),
                            "wdId": "w1",
                            "state": "2",
                        },
                        {  # stablecoins carry more decimals than dollars; a dust fee is no line
                            "ccy": "USDT",
                            "amt": "996.47877",
                            "fee": "0.00019",
                            "ts": ms("2026-10-21T00:00:00"),
                            "wdId": "w2",
                            "state": "2",
                        },
                    ],
                },
            )
        raise AssertionError(p)

    r = run_sync(conn, Okx(), client_factory=mock(handle), now=NOW)
    assert (r.ok, r.new) == (True, 4)
    got = [(d, a) for d, a, _ in lines(conn, "Assets:OKX:%")]
    assert got == [
        ("2026-10-10", 20000),
        ("2026-10-20", -5000),
        ("2026-10-20", -100),
        ("2026-10-21", -99648),
    ]
    assert conn.execute(text("SELECT value FROM valuations")).scalar_one() == 25075


# ---------------------------------------------------------------- API
def test_connections_api_never_returns_secrets(env: Path) -> None:
    from pf.api.app import app

    with TestClient(app) as c:
        listed = {x["source"]: x for x in c.get("/api/connections").json()}
        assert set(listed) == {"plaid", "simplefin", "snaptrade", "ibkr_flex", "okx", "mailbox"}
        assert not listed["ibkr_flex"]["connected"]
        assert (
            c.put("/api/connections/ibkr_flex", json={"values": {"token": "t"}}).status_code == 400
        )
        saved = c.put(
            "/api/connections/ibkr_flex",
            json={"values": {"token": "secret-token", "query_id": "99"}},
        )
        assert saved.status_code == 200 and saved.json()["connected"]
        assert "secret-token" not in json.dumps(c.get("/api/connections").json())
        assert {f["name"]: f["filled"] for f in saved.json()["fields"]} == {
            "token": True,
            "query_id": True,
        }
        assert not c.delete("/api/connections/ibkr_flex").json()["connected"]


# ---------------------------------------------------------------- mailbox
def test_mailbox_collects_bill_attachments_read_only(
    conn: Connection, env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from email.message import EmailMessage

    from pf.connectors.mailbox import Mailbox

    cn = Path(__file__).resolve().parents[2] / "fixtures" / "cn"
    bill = EmailMessage()
    bill["Subject"] = "支付宝交易流水证明"
    bill.set_content("见附件")
    bill.add_attachment(
        (cn / "alipay_record_202609.zip").read_bytes(),
        maintype="application",
        subtype="zip",
        filename="支付宝交易明细.zip",
    )
    other = EmailMessage()
    other["Subject"] = "Photos"
    other.set_content("hi")
    other.add_attachment(b"\x89PNG", maintype="image", subtype="png", filename="cat.png")
    other.add_attachment(
        b"name,color\ncat,blue\n", maintype="text", subtype="csv", filename="pets.csv"
    )
    wechat = EmailMessage()
    wechat["From"] = "WeChat Pay <wechatpay@tencent.com>"
    wechat["Subject"] = "微信支付-账单流水文件"
    wechat.set_content(
        '<a href="https://tenpay.wechatpay.cn.example.test/userroll/userbilldownload/downloadfilefromemail?id=fake">skip</a>'
        '<a href="https://tenpay.wechatpay.cn/userroll/userbilldownload/downloadfilefromemail?id=demo&amp;mode=test">download</a>',
        subtype="html",
    )
    messages = {"1": bill.as_bytes(), "2": other.as_bytes(), "3": wechat.as_bytes()}
    downloads = []

    def download(url: str) -> bytes:
        downloads.append(url)
        return (cn / "wechat_bill_202609.zip").read_bytes()

    monkeypatch.setattr("pf.connectors.mailbox._download_wechat", download)
    log: list[str] = []

    class FakeImap:
        def __init__(self, host: str) -> None:
            assert host == "imap.qq.com"

        def login(self, user: str, pw: str) -> None:
            assert (user, pw) == ("me@qq.com", "app-pw")

        def select(self, box: str, readonly: bool) -> None:
            assert readonly
            log.append("select")

        def uid(self, cmd: str, *args: object) -> tuple[str, list[object]]:
            if cmd == "SEARCH":
                return "OK", [b"1 2 3"]
            assert cmd == "FETCH" and args[1] == "(BODY.PEEK[])"  # PEEK: never marks read
            return "OK", [(b"1 (BODY[]", messages[str(args[0])]), b")"]

        def logout(self) -> None:
            log.append("logout")

    Secrets.set("mailbox", {"address": "me@qq.com", "password": "app-pw"})
    save_state(conn, {"mailbox": {"cursor": {"seen": ["imap.qq.com:3"]}}})
    r = run_sync(
        conn, Mailbox(imap=FakeImap), client_factory=mock(lambda req: httpx.Response(500)), now=NOW
    )  # type: ignore[arg-type]
    assert r.ok and r.accounts == 2 and log == ["select", "logout"]
    assert downloads == [
        "https://tenpay.wechatpay.cn/userroll/userbilldownload/downloadfilefromemail?id=demo&mode=test"
    ]
    pending = conn.execute(
        text("SELECT reason, institution FROM pending_imports ORDER BY institution")
    ).all()
    assert [tuple(p) for p in pending] == [
        ("needs_password", "Alipay"),
        ("needs_password", "WeChat"),
    ]  # the locked zip waits for its code
    again = run_sync(
        conn, Mailbox(imap=FakeImap), client_factory=mock(lambda req: httpx.Response(500)), now=NOW
    )  # type: ignore[arg-type]
    assert again.ok and again.accounts == 0  # seen messages are not fetched twice


def test_two_cards_of_one_bank_stay_two_accounts_and_resync_keeps_one_balance(
    conn: Connection,
) -> None:
    """Regression (real Chase/BofA link): a second card must not fall into the first card's
    account, and syncing again the same day must not stack balance assertions."""

    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/transactions/sync":
            body = json.loads(req.content)
            added = (
                []
                if body.get("cursor")
                else [
                    {
                        "transaction_id": "a1",
                        "account_id": "c1",
                        "amount": 10,
                        "date": "2026-10-20",
                        "name": "CAFE",
                        "pending": False,
                    },
                    {
                        "transaction_id": "b1",
                        "account_id": "c2",
                        "amount": 20,
                        "date": "2026-10-21",
                        "name": "BOOKS",
                        "pending": False,
                    },
                ]
            )
            return httpx.Response(
                200,
                json={
                    "added": added,
                    "modified": [],
                    "removed": [],
                    "next_cursor": "C",
                    "has_more": False,
                },
            )
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {
                        "account_id": "c1",
                        "name": "Freedom",
                        "mask": "0004",
                        "type": "credit",
                        "balances": {"current": 10},
                    },
                    {
                        "account_id": "c2",
                        "name": "Sapphire",
                        "mask": "0002",
                        "type": "credit",
                        "balances": {"current": 20},
                    },
                ]
            },
        )

    Secrets.set(
        "plaid",
        {
            "client_id": "cid",
            "secret": "sec",
            "items": [{"item_id": "I1", "access_token": "a", "institution": "Chase"}],
        },
    )
    assert run_sync(conn, Plaid(), client_factory=mock(handle), now=NOW).ok
    assert run_sync(conn, Plaid(), client_factory=mock(handle), now=NOW).ok  # same day again
    cards = dict(
        conn.execute(
            text("SELECT name, id FROM accounts WHERE type='Liabilities' AND institution='Chase'")
        ).all()
    )
    assert sorted(cards) == ["Liabilities:Chase:Card-0002", "Liabilities:Chase:Card-0004"]
    asserted = conn.execute(
        text(
            "SELECT a.name, COUNT(*), MIN(b.amount) FROM balance_assertions b JOIN accounts a ON a.id=b.account_id GROUP BY a.name ORDER BY a.name"
        )
    ).all()
    assert [tuple(r) for r in asserted] == [
        ("Liabilities:Chase:Card-0002", 1, -2000),
        ("Liabilities:Chase:Card-0004", 1, -1000),
    ]
    assert reconcile(conn) == []


def test_snaptrade_personal_key_needs_no_user_and_sends_json(conn: Connection) -> None:
    """A Personal key ('PERS-…') is the user: no registerUser, no userId/userSecret params."""
    from pf.connectors.snaptrade import portal_url

    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert "userId" not in req.url.params and "userSecret" not in req.url.params
        p = req.url.path
        if p == "/api/v1/snapTrade/login":
            return httpx.Response(200, json={"redirectURI": "https://app.snaptrade.com/connect"})
        if p == "/api/v1/authorizations":
            return httpx.Response(200, json=[])
        if p == "/api/v1/accounts":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "A1",
                        "name": "Individual",
                        "number": "X1234",
                        "institution_name": "Charles Schwab",
                        "balance": {"total": {"amount": 100, "currency": "USD"}},
                    }
                ],
            )
        if p.endswith("/activities"):
            return httpx.Response(200, json={"data": [], "pagination": {"total": 0}})
        if p.endswith("/positions/all"):
            return httpx.Response(200, json={"results": []})
        raise AssertionError(p)

    Secrets.set("snaptrade", {"client_id": "PERS-ABC", "consumer_key": "k"})
    r = run_sync(conn, SnapTrade(), client_factory=mock(handle), now=NOW)
    assert r.ok and r.accounts == 1
    assert not any(q.url.path.endswith("registerUser") for q in seen)
    assert (
        conn.execute(
            text("SELECT name FROM accounts WHERE institution = 'Charles Schwab'")
        ).scalar_one()
        == "Assets:Charles Schwab:Brokerage-1234"
    )
    with mock(handle)() as client:
        assert (
            portal_url(client, {"client_id": "PERS-ABC", "consumer_key": "k"})
            == "https://app.snaptrade.com/connect"
        )


def test_post_with_body_declares_json() -> None:
    from pf.connectors.snaptrade import SnapTradeApi

    def handle(req: httpx.Request) -> httpx.Response:
        assert req.headers["Content-Type"] == "application/json"  # SnapTrade answers 415 without it
        return httpx.Response(200, json={"userId": "u", "userSecret": "s"})

    with mock(handle)() as client:
        assert (
            SnapTradeApi(client, "c", "k").call(
                "POST", "/snapTrade/registerUser", body={"userId": "u"}
            )["userSecret"]
            == "s"
        )


def test_tidy_names_renames_connector_accounts_everywhere(conn: Connection) -> None:
    from pf.ledger.rename import rename_account, tidy_connector_names

    Secrets.set(
        "plaid",
        {
            "client_id": "cid",
            "secret": "sec",
            "items": [{"item_id": "I1", "access_token": "a", "institution": "Chase"}],
        },
    )
    assert run_sync(conn, Plaid(), client_factory=mock(plaid_handler()), now=NOW).ok
    rename_account(
        conn, "Liabilities:Chase:Card-0002", "Liabilities:Chase:Sapphire Preferred Long Name-0002"
    )  # an old-style name
    conn.execute(
        text(
            "INSERT INTO account_bindings(importer, hint, account_name) VALUES ('chase_credit', '0002', 'Liabilities:Chase:Sapphire Preferred Long Name-0002')"
        )
    )
    assert tidy_connector_names(conn) == [
        ("Liabilities:Chase:Sapphire Preferred Long Name-0002", "Liabilities:Chase:Card-0002")
    ]
    assert (
        conn.execute(text("SELECT account_name FROM account_bindings")).scalar_one()
        == "Liabilities:Chase:Card-0002"
    )
    assert tidy_connector_names(conn) == []
    with pytest.raises(ValueError):
        rename_account(conn, "Liabilities:Chase:Card-0002", "Assets:Chase:Card-0002")


def test_sync_now_runs_one_at_a_time_and_commits_each_source(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A click on 立即同步 during the daily sync waits instead of hitting a locked ledger."""
    import threading
    import time

    import pf.connectors as connectors
    from pf.connectors.base import SyncResult
    from pf.db import make_engine

    running = 0
    overlap = False

    def fake(conn: Connection, connector: object, **_: object) -> SyncResult:
        nonlocal running, overlap
        running += 1
        overlap = overlap or running > 1
        conn.execute(
            text("INSERT INTO owner_names(name, source) VALUES (:n, 'user')"),
            {"n": f"n{time.time_ns()}"},
        )
        time.sleep(0.15)
        running -= 1
        return SyncResult("okx", True)

    monkeypatch.setattr(connectors, "run_sync", fake)
    monkeypatch.setattr(connectors, "connected", lambda: ["okx", "plaid"])
    threads = [threading.Thread(target=connectors.sync_now) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not overlap
    with make_engine().connect() as c:
        assert (
            c.execute(text("SELECT COUNT(*) FROM owner_names WHERE name LIKE 'n%'")).scalar_one()
            == 6
        )


# ---------------------------------------------------------------- SimpleFIN
def _epoch(day: str) -> int:
    return int(dt.datetime.fromisoformat(day + "T12:00:00").timestamp())


SFIN_TOKEN = base64.b64encode(b"https://bridge.example/simplefin/claim/abc").decode()


def _sfin(
    accounts: dict, seen: list[httpx.Request], claims: list[int]
) -> Callable[[], httpx.Client]:
    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/claim/abc"):
            claims.append(1)
            if len(claims) > 1:
                return httpx.Response(403, text="Forbidden")
            return httpx.Response(200, text="https://user1:secret@bridge.example/simplefin")
        assert req.url.path == "/simplefin/accounts"
        assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"user1:secret").decode()
        return httpx.Response(200, json=accounts)

    return mock(handle)


def test_simplefin_claims_once_then_books_bank_card_and_brokerage(
    conn: Connection, secrets_file: Path
) -> None:
    Secrets.set("simplefin", {"token": SFIN_TOKEN})
    doc = {
        "errlist": [{"code": "act.missingdata", "msg": "Incomplete transaction listing. Try again later.", "account_id": "c1"}],
        "connections": [
            {"conn_id": "CON-1", "name": "Testbank - Jo", "org_id": "ORG-1", "org_name": "Testbank", "sfin_url": "https://x"},
            {"conn_id": "CON-2", "name": "Brokerco", "org_id": "ORG-2", "sfin_url": "https://y"},
        ],
        "accounts": [
            {"id": "a1", "name": "Everyday Checking (4321)", "conn_id": "CON-1", "currency": "USD",
             "balance": "1200.50", "balance-date": _epoch("2026-10-30"), "transactions": [
                 {"id": "t1", "posted": _epoch("2026-10-28"), "amount": "2000.00", "description": "ACME PAYROLL"},
                 {"id": "t2", "posted": _epoch("2026-10-29"), "amount": "-799.50", "description": "RENT CO", "payee": "Rent Co"},
                 {"id": "t3", "posted": 0, "amount": "-12.00", "description": "COFFEE", "pending": True},
             ]},
            {"id": "c1", "name": "Rewards Visa 9876", "conn_id": "CON-1", "currency": "USD",
             "balance": "-45.10", "balance-date": _epoch("2026-10-30"), "transactions": [
                 {"id": "t9", "posted": _epoch("2026-10-27"), "amount": "-45.10", "description": "GROCER"},
             ]},
            {"id": "b1", "name": "Individual Brokerage 5555", "conn_id": "CON-2", "currency": "USD",
             "balance": "3100.00", "balance-date": _epoch("2026-10-30"), "transactions": [
                 {"id": "x1", "posted": _epoch("2026-10-20"), "amount": "-500.00", "description": "YOU BOUGHT QQQ"}],
             "holdings": [{"symbol": "QQQ", "shares": "5", "market_value": "3000.00", "cost_basis": "2500.00", "currency": "USD"}]},
            {"id": "m1", "name": "Miles", "conn_id": "CON-1", "currency": "https://air.example/miles",
             "balance": "52000", "balance-date": _epoch("2026-10-30"), "transactions": []},
        ],
    }  # fmt: skip
    seen: list[httpx.Request] = []
    claims: list[int] = []
    r = run_sync(conn, SimpleFin(), client_factory=_sfin(doc, seen, claims), now=NOW)
    assert (r.ok, r.accounts, r.new, r.partial) == (True, 3, 3, True)  # miles are not money
    assert "Incomplete transaction listing" in (r.message or "")
    # The token is spent: only the access URL is kept, and the next sync does not claim again.
    assert Secrets.get("simplefin") == {
        "access_url": "https://user1:secret@bridge.example/simplefin"
    }
    assert lines(conn, "Assets:Testbank:Checking-4321") == [
        ("2026-10-28", 200000, "ACME PAYROLL"),
        ("2026-10-29", -79950, "RENT CO"),
    ]  # the pending coffee waits until it posts
    assert lines(conn, "Liabilities:Testbank:Card-9876") == [("2026-10-27", -4510, "GROCER")]
    assert reconcile(conn) == []
    # The brokerage account is booked by its value; its positions are kept, its trades are not cash.
    from pf.reports.portfolio import positions

    held = positions(conn).accounts[0]
    assert (held.account, held.positions[0].symbol, held.positions[0].value, held.positions[0].cost) == (
        "Assets:Brokerco:Brokerage-5555", "QQQ", "3000.00", "2500.00")  # fmt: skip
    assert lines(conn, "Assets:Brokerco:Brokerage-5555") == []

    first = dict(seen[1].url.params)
    assert first["version"] == "2" and int(first["start-date"]) < _epoch("2026-10-01")
    again = run_sync(
        conn, SimpleFin(), client_factory=_sfin(doc, seen, claims), now=NOW + dt.timedelta(days=1)
    )
    assert (again.ok, again.new, len(claims)) == (True, 0, 1)  # same ids: nothing booked twice


def test_simplefin_version_1_shape_and_a_spent_token(conn: Connection, secrets_file: Path) -> None:
    Secrets.set("simplefin", {"token": SFIN_TOKEN})
    doc = {
        "errors": ["Connection to Oldbank may need attention"],
        "accounts": [
            {"org": {"domain": "oldbank.example", "name": "Oldbank"}, "id": "s1", "name": "Savings",
             "currency": "USD", "balance": "10.00", "balance-date": _epoch("2026-10-30"),
             "transactions": [{"id": "i1", "posted": _epoch("2026-10-30"), "amount": "10.00", "description": "INTEREST"}]},
        ],
    }  # fmt: skip
    seen: list[httpx.Request] = []
    r = run_sync(conn, SimpleFin(), client_factory=_sfin(doc, seen, []), now=NOW)
    assert (r.ok, r.new, r.message) == (True, 1, "Connection to Oldbank may need attention")
    assert lines(conn, "Assets:Oldbank:Savings") == [("2026-10-30", 1000, "INTEREST")]

    # A token someone already used: say so, and ask for a new one.
    Secrets.set("simplefin", {"token": SFIN_TOKEN})
    spent = run_sync(conn, SimpleFin(), client_factory=_sfin(doc, seen, [1]), now=NOW)
    assert (spent.ok, spent.reauth) == (False, True)
    Secrets.set("simplefin", {"token": "not base64 at all !!"})
    assert run_sync(conn, SimpleFin(), client_factory=_sfin(doc, seen, []), now=NOW).reauth
