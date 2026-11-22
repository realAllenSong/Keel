"""SnapTrade Personal (free, up to 20 brokerage connections): Fidelity, Schwab, IBKR, ...

Keys from the SnapTrade dashboard (client id + consumer key). A Personal key (client id
'PERS-…') is itself the user: brokerages are connected in the SnapTrade dashboard or the
connection portal, and requests carry no userId / userSecret. A commercial key needs one
registered SnapTrade user for this Mac (`register`). Each sync lists the accounts, their total
value, positions and activities since the last sync.

Requests are signed as SnapTrade documents ("Request signatures"): HMAC-SHA256 with the consumer
key over the canonical JSON {"content": body|null, "path": "/api/v1/...", "query": "<raw query>"}
(sorted keys, no spaces), base64, in the Signature header.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import re
import time
import uuid
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from pf.connectors.base import AccountStatement, ConnectorError, FetchResult, Field, Secrets
from pf.i18n import tr
from pf.importers.brokers import DIVIDENDS, FEES, INTEREST, TAXES

BASE = "https://api.snaptrade.com"
FIRST_SYNC_DAYS = 3 * 365  # history on first sync; later syncs overlap a week (dedup by id)

# Activity type → category (None = money crossing the account's edge: left for transfer pairing).
KINDS: dict[str, str | None] = {
    "DIVIDEND": DIVIDENDS,
    "STOCK_DIVIDEND": "skip",
    "INTEREST": INTEREST,
    "FEE": FEES,
    "TAX": TAXES,
    "CONTRIBUTION": None,
    "WITHDRAWAL": None,
    "TRANSFER": None,
}


# Activity type → what happened to a position (kept as trades, never booked as income).
TRADES: dict[str, str] = {
    "BUY": "BUY",
    "SELL": "SELL",
    "REI": "REINVEST",
    "SPLIT": "SPLIT",
    "STOCK_DIVIDEND": "STOCK_DIVIDEND",
    "OPTIONASSIGNMENT": "ASSIGNMENT",
    "OPTIONEXERCISE": "EXERCISE",
    "OPTIONEXPIRATION": "EXPIRATION",
}


def trade(a: dict[str, Any], currency: str) -> dict[str, Any] | None:
    """One activity as a trade record, or None when it is not about a position."""
    action = TRADES.get((a.get("type") or "").upper())
    day = _day(a.get("trade_date") or a.get("settlement_date"))
    if not action or not day or not a.get("id"):
        return None
    sym, opt = a.get("symbol") or {}, a.get("option_symbol") or {}
    if opt:
        symbol = " ".join(str(opt.get("ticker") or "").split())
        underlying = (opt.get("underlying_symbol") or {}).get("symbol")
        kind = "option"
    else:
        symbol = sym.get("symbol") or sym.get("raw_symbol") or ""
        underlying = None
        kind = ((sym.get("type") or {}).get("code") or "").lower() or None
    if not symbol:
        return None

    def num(key: str) -> str | None:
        return str(Decimal(str(a[key]))) if a.get(key) is not None else None

    return {
        "id": a["id"],
        "date": day,
        "action": action,
        "symbol": symbol,
        "underlying": underlying,
        "kind": kind,
        "quantity": num("units") or "0",
        "price": num("price"),
        # A split moves no cash, whatever the broker prints in the amount column.
        "amount": "0" if action == "SPLIT" else (num("amount") or "0"),
        "fee": num("fee"),
        "currency": (a.get("currency") or {}).get("code") or currency,
        "description": a.get("description") or "",
    }


CRYPTO_EXCHANGES = re.compile(
    r"kraken|coinbase|binance|gemini|crypto\.com|okx|bitstamp|kucoin", re.I
)


def signature(consumer_key: str, path: str, query: str, body: Any | None) -> str:
    content = body if body not in (None, {}) else None
    payload = json.dumps(
        {"content": content, "path": path, "query": query}, separators=(",", ":"), sort_keys=True
    )
    digest = hmac.new(consumer_key.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class SnapTradeApi:
    def __init__(self, client: httpx.Client, client_id: str, consumer_key: str) -> None:
        self.client, self.client_id, self.consumer_key = client, client_id, consumer_key

    def call(
        self, method: str, path: str, params: dict[str, str] | None = None, body: Any = None
    ) -> Any:
        query = urlencode(
            {"clientId": self.client_id, "timestamp": str(int(time.time())), **(params or {})}
        )
        full = f"/api/v1{path}"
        headers = {"Signature": signature(self.consumer_key, full, query, body)}
        if body not in (None, {}):
            headers["Content-Type"] = "application/json"
        resp = self.client.request(
            method,
            f"{BASE}{full}?{query}",
            headers=headers,
            content=json.dumps(body, separators=(",", ":")) if body not in (None, {}) else None,
        )
        if resp.status_code in (401, 403):
            raise ConnectorError(f"SnapTrade refused the keys ({resp.status_code})", reauth=True)
        resp.raise_for_status()
        return resp.json()


def personal(secrets: dict[str, Any]) -> bool:
    """Personal API keys ('PERS-…') carry the user themselves: no registration, no user params."""
    return str(secrets.get("client_id", "")).upper().startswith("PERS-")


def _user(secrets: dict[str, Any]) -> dict[str, str]:
    if personal(secrets):
        return {}
    return {"userId": secrets["user_id"], "userSecret": secrets["user_secret"]}


def register(client: httpx.Client, client_id: str, consumer_key: str) -> dict[str, str]:
    """Create this Mac's SnapTrade user once; returns the secrets to keep in the Keychain."""
    user_id = f"keel-{uuid.uuid4()}"
    api = SnapTradeApi(client, client_id, consumer_key)
    got = api.call("POST", "/snapTrade/registerUser", body={"userId": user_id})
    return {
        "client_id": client_id,
        "consumer_key": consumer_key,
        "user_id": got["userId"],
        "user_secret": got["userSecret"],
    }


def portal_url(client: httpx.Client, secrets: dict[str, Any]) -> str:
    """The connection portal where the user signs in to each brokerage."""
    api = SnapTradeApi(client, secrets["client_id"], secrets["consumer_key"])
    got = api.call("POST", "/snapTrade/login", params=_user(secrets))
    return str(got["redirectURI"])


def _day(s: str | None) -> str | None:
    return s[:10] if s else None


class SnapTrade:
    source = "snaptrade"
    label = "SnapTrade (Fidelity / Schwab / IBKR)"
    fields = (
        Field("client_id", "Client ID", secret=False),
        Field("consumer_key", "Consumer Key"),
    )

    def fetch(
        self, client: httpx.Client, secrets: dict[str, Any], state: dict[str, Any]
    ) -> FetchResult:
        if not personal(secrets) and "user_secret" not in secrets:  # commercial: register once
            secrets = register(client, secrets["client_id"], secrets["consumer_key"])
            Secrets.set(self.source, secrets)
        api = SnapTradeApi(client, secrets["client_id"], secrets["consumer_key"])
        user = _user(secrets)
        today = dt.date.today()
        out = []
        warnings = []
        accounts = api.call("GET", "/accounts", params=user)
        disabled = set()
        try:
            for auth in api.call("GET", "/authorizations", params=user):
                broker = auth.get("brokerage") or {}
                name = broker.get("display_name") or broker.get("name") or "Brokerage"
                if auth.get("disabled"):
                    disabled.add(auth["id"])
                    warnings.append(
                        tr("{name}：授权已失效，保留上次数据；请在 SnapTrade 重新连接。", name=name)
                    )
                elif not any(a.get("brokerage_authorization") == auth["id"] for a in accounts):
                    warnings.append(
                        tr(
                            "{name}：已授权，但尚未返回任何账户；资产尚未计入，不代表余额为 0。",
                            name=name,
                        )
                    )
                    if "interactive" in name.lower() or "ibkr" in name.lower():
                        warnings.append(
                            tr(
                                "IBKR 首次启用通常需 24–48 小时，周末可到 72 小时；超过 72 小时仍无账户，请核对 IBKR 的 Third-Party Reports → SnapTrade，并联系 SnapTrade 支持。"
                            )
                        )
        except (ConnectorError, httpx.HTTPError):
            warnings.append(tr("已读取账户，但无法核对券商授权状态；请稍后重试。"))
        if not accounts:
            warnings.append(tr("SnapTrade 尚未返回账户数据；已连接不代表已完成首次同步。"))
        for acc in accounts:
            acc_id = acc["id"]
            if acc.get("brokerage_authorization") in disabled:
                continue  # cached data from a disabled connection is not a fresh valuation
            # Investment accounts only: a broker's bank account (Schwab Investor Checking) is a
            # bank account, and banks come through Plaid. Booking it twice would double-count it.
            kind_text = f"{acc.get('name') or ''} {acc.get('raw_type') or ''}".lower()
            if re.search(r"checking|savings|bank|deposit", kind_text):
                continue
            total = (acc.get("balance") or {}).get("total") or {}
            sync = acc.get("sync_status") or {}
            holdings_ready = (sync.get("holdings") or {}).get("initial_sync_completed") is not False
            transactions_ready = (sync.get("transactions") or {}).get(
                "initial_sync_completed"
            ) is not False
            name = acc.get("institution_name") or "Brokerage"
            if not holdings_ready or not transactions_ready:
                warnings.append(
                    tr(
                        "{name}：首次同步尚未完成，收支或资产数据可能不完整；请稍后同步。",
                        name=name,
                    )
                )
            currency = total.get("currency") or "USD"
            number = re.sub(r"\D", "", str(acc.get("number") or ""))  # 'XXX130' / '...130' -> '130'
            st = AccountStatement(
                source=self.source,
                account_id=acc_id,
                institution=acc.get("institution_name") or "Brokerage",
                type="Assets",
                currency=currency,
                name=acc.get("name") or "Brokerage",
                mask=number[-4:] or None,
                # A crypto exchange linked through SnapTrade is not a stock brokerage.
                asset_class="crypto" if CRYPTO_EXCHANGES.search(name) else "brokerage",
            )
            if holdings_ready and total.get("amount") is not None:
                st.valuations.append(
                    {"date": today.isoformat(), "value": str(Decimal(str(total["amount"])))}
                )
            elif holdings_ready:
                warnings.append(tr("{name}：账户总值尚未返回，保留上次市值。", name=name))
            # Trades were not kept before: an account synced only for cash activity reads its
            # whole history once more (cash lines already booked are skipped by their ids).
            last = state.get(acc_id) if state.get(f"{acc_id}:trades") else None
            start = (
                dt.date.fromisoformat(last) - dt.timedelta(days=7)
                if last
                else today - dt.timedelta(days=FIRST_SYNC_DAYS)
            )
            offset = 0
            while transactions_ready:
                page = api.call(
                    "GET",
                    f"/accounts/{acc_id}/activities",
                    params={
                        **user,
                        "startDate": start.isoformat(),
                        "endDate": today.isoformat(),
                        "offset": str(offset),
                        "limit": "1000",
                    },
                )
                items = page.get("data", []) if isinstance(page, dict) else page
                for a in items:
                    if (t := trade(a, currency)) is not None:
                        st.trades.append(t)
                    kind = KINDS.get((a.get("type") or "").upper(), "skip")
                    amount = a.get("amount")
                    day = _day(a.get("settlement_date") or a.get("trade_date"))
                    if kind == "skip" or amount in (None, 0) or not day:
                        continue
                    if (a.get("currency") or {}).get("code", currency) != currency:
                        continue  # other-currency cash: the total value mark carries it
                    st.transactions.append(
                        {
                            "id": a.get("id"),
                            "date": day,
                            "amount": str(Decimal(str(amount)).quantize(Decimal("0.01"))),
                            "description": a.get("description") or a.get("type") or "",
                            "category": kind,
                        }
                    )
                total_n = (
                    (page.get("pagination") or {}).get("total", 0) if isinstance(page, dict) else 0
                )
                offset += len(items)
                if not items or offset >= total_n:
                    break
            try:
                positions = (
                    api.call("GET", f"/accounts/{acc_id}/positions/all", params=user)
                    if holdings_ready
                    else {}
                )
                st.holdings_complete = holdings_ready
                st.holdings_as_of = today.isoformat()
                for pos in positions.get("results", []):
                    instrument = pos.get("instrument") or {}
                    st.holdings.append(
                        {
                            "symbol": instrument.get("symbol") or instrument.get("raw_symbol"),
                            "kind": instrument.get("kind"),
                            "quantity": str(pos["units"]) if pos.get("units") is not None else None,
                            "price": str(pos["price"]) if pos.get("price") is not None else None,
                            # SnapTrade's cost basis is the average price paid per unit.
                            "cost_basis": str(pos["cost_basis"])
                            if pos.get("cost_basis") is not None
                            else None,
                            "cost_basis_per_unit": True,
                            "currency": pos.get("currency")
                            or instrument.get("currency")
                            or currency,
                            "cash_equivalent": bool(pos.get("cash_equivalent")),
                            "as_of": (positions.get("data_freshness") or {}).get("as_of"),
                        }
                    )
            except httpx.HTTPStatusError:
                st.holdings, st.holdings_complete = [], False
                warnings.append(
                    tr("{name}：持仓明细读取失败；已返回的账户总值和收支仍可导入。", name=name)
                )
            if transactions_ready:
                state[acc_id] = today.isoformat()
                state[f"{acc_id}:trades"] = today.isoformat()
            out.append(st)
        return FetchResult(out, warnings)
