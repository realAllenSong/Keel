"""Plaid (Trial plan: free, up to 10 Items) for banks and cards: Chase, Amex, Bank of America.

An Item is one bank login (one Chase login = every Chase account). Linking uses Plaid's Hosted
Link: Keel creates a link token, opens hosted_link_url in the browser, and collects the result
with /link/token/get; the access token goes to the Keychain with the institution's name.

Each sync, per Item: /transactions/sync from the saved cursor (posted lines only; a pending line
arrives again once posted) and /accounts/get for balances. Plaid amounts are positive when money
leaves the account, for deposit accounts and cards alike, so the ledger amount is -amount.
A balance becomes a balance assertion only when the account has no pending lines: whether
Plaid's current balance already includes them differs by bank.

Pending lines are never booked. Each sync also asks /transactions/get for the last 30 days and
reports the lines still pending, with each cash account's current and available balance, so the
app can show money in flight beside the books (pf/ledger/in_flight.py).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import httpx

from pf.connectors.base import AccountStatement, ConnectorError, Field, Secrets
from pf.i18n import tr

HOSTS = {"production": "https://production.plaid.com", "sandbox": "https://sandbox.plaid.com"}
REAUTH_CODES = {
    "ITEM_LOGIN_REQUIRED",
    "INVALID_ACCESS_TOKEN",
    "PENDING_EXPIRATION",
    "INVALID_API_KEYS",
}
KIND = {"depository": "Assets", "credit": "Liabilities", "loan": "Liabilities"}
PENDING_DAYS = 30
TRANSFERS = {"TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS"}
# Merchants that authorise more than they finally charge, or release the hold: a deposit.
HOLDS = {"TRAVEL_LODGING", "TRAVEL_RENTAL_CARS", "TRANSPORTATION_GAS"}


class PlaidApi:
    def __init__(self, client: httpx.Client, secrets: dict[str, Any]) -> None:
        self.client = client
        self.host = HOSTS[secrets.get("environment") or "production"]
        self.auth = {"client_id": secrets["client_id"], "secret": secrets["secret"]}

    def call(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self.client.post(f"{self.host}{path}", json={**self.auth, **body})
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            code = data.get("error_code", str(resp.status_code))
            msg = data.get("display_message") or data.get("error_message") or code
            raise ConnectorError(f"Plaid {code}: {msg}", reauth=code in REAUTH_CODES)
        return data


def start_link(client: httpx.Client, secrets: dict[str, Any]) -> dict[str, str]:
    """A hosted link for one more bank login; open `url`, then call finish_link(link_token)."""
    got = PlaidApi(client, secrets).call(
        "/link/token/create",
        {
            "client_name": "Keel",
            "user": {"client_user_id": "keel-owner"},
            "products": ["transactions"],
            "optional_products": ["liabilities"],
            "transactions": {"days_requested": 730},
            "country_codes": ["US"],
            "language": "en",
            "hosted_link": {},
        },
    )
    return {"link_token": got["link_token"], "url": got["hosted_link_url"]}


def finish_link(client: httpx.Client, secrets: dict[str, Any], link_token: str) -> list[str]:
    """Exchange what the hosted link produced; returns the institutions added (empty = not yet)."""
    api = PlaidApi(client, secrets)
    got = api.call("/link/token/get", {"link_token": link_token})
    added = []
    items: list[dict[str, str]] = secrets.setdefault("items", [])
    for session in got.get("link_sessions") or []:
        for res in (session.get("results") or {}).get("item_add_results") or []:
            ex = api.call("/item/public_token/exchange", {"public_token": res["public_token"]})
            if any(i["item_id"] == ex["item_id"] for i in items):
                continue
            name = (res.get("institution") or {}).get("name") or "Bank"
            items.append(
                {"item_id": ex["item_id"], "access_token": ex["access_token"], "institution": name}
            )  # type: ignore[union-attr]
            added.append(name)
    if added:
        Secrets.set("plaid", secrets)
    return added


class Plaid:
    source = "plaid"
    label = "Plaid (Chase / Amex / Bank of America)"
    fields = (
        Field("client_id", "client_id", secret=False),
        Field("secret", "secret"),
    )

    def fetch(
        self, client: httpx.Client, secrets: dict[str, Any], state: dict[str, Any]
    ) -> list[AccountStatement]:
        api = PlaidApi(client, secrets)
        items = secrets.get("items") or []
        if not items:
            raise ConnectorError(tr("还没有连接银行：点「添加银行」登录一家。"))
        today = dt.date.today().isoformat()
        out: list[AccountStatement] = []
        for item in items:
            cursor = state.get(item["item_id"])
            # Pending lines seen in earlier syncs, by id → account, until they post or vanish.
            pending: dict[str, str] = dict(state.get(f"pending:{item['item_id']}", {}))
            added: list[dict[str, Any]] = []
            while True:
                body: dict[str, Any] = {"access_token": item["access_token"], "count": 500}
                if cursor:
                    body["cursor"] = cursor
                page = api.call("/transactions/sync", body)
                # 'modified' is skipped: a changed amount or text would book the line twice.
                added += page.get("added", [])
                for r in page.get("removed", []):
                    pending.pop(r.get("transaction_id", ""), None)
                cursor = page.get("next_cursor")
                if not page.get("has_more"):
                    break
            for t in added:
                if t.get("pending"):
                    pending[t["transaction_id"]] = t.get("account_id", "")
                elif t.get("pending_transaction_id"):
                    pending.pop(t["pending_transaction_id"], None)
            state[item["item_id"]] = cursor
            state[f"pending:{item['item_id']}"] = pending
            pending_accounts = set(pending.values())
            accounts = api.call("/accounts/get", {"access_token": item["access_token"]})["accounts"]
            by_id: dict[str, AccountStatement] = {}
            for a in accounts:
                kind = KIND.get(a.get("type", ""))
                if kind is None:
                    continue  # investment accounts come through SnapTrade / IBKR
                bal = a.get("balances") or {}
                by_id[a["account_id"]] = AccountStatement(
                    source=self.source,
                    account_id=a["account_id"],
                    institution=item.get("institution") or "Bank",
                    type=kind,
                    currency=bal.get("iso_currency_code") or "USD",
                    name=a.get("official_name") or a.get("name"),
                    mask=a.get("mask"),
                    asset_class="cash" if kind == "Assets" else None,
                )
            for t in added:
                st = by_id.get(t.get("account_id", ""))
                if st is None:
                    continue
                if t.get("pending"):
                    continue
                pfc = t.get("personal_finance_category") or {}
                st.transactions.append(
                    {
                        "id": t["transaction_id"],
                        "date": t["date"],
                        "amount": str(-Decimal(str(t["amount"]))),
                        "description": t.get("original_description") or t.get("name") or "",
                        "payee": t.get("merchant_name"),
                        "category_hint": pfc.get("detailed") or pfc.get("primary"),
                    }
                )
            in_flight = _pending_lines(api, item["access_token"], today)
            for a in accounts:
                st = by_id.get(a["account_id"])
                if st is None:
                    continue
                if in_flight is not None:
                    st.pending = in_flight.get(a["account_id"], [])
                bal = a.get("balances") or {}
                cur, avail = bal.get("current"), bal.get("available")
                if st.type == "Assets" and cur is not None and avail is not None:
                    st.current = str(Decimal(str(cur)))
                    st.available = str(Decimal(str(avail)))
            for a in accounts:
                st = by_id.get(a["account_id"])
                current = (a.get("balances") or {}).get("current")
                if st is None or current is None or a["account_id"] in pending_accounts:
                    continue
                value = Decimal(str(current))
                st.balances.append(
                    {"date": today, "amount": str(value if st.type == "Assets" else -value)}
                )
            out += by_id.values()
        return out


def _pending_lines(api: PlaidApi, token: str, today: str) -> dict[str, list[dict[str, Any]]] | None:
    """Lines the bank still shows as pending, by Plaid account id; None if Plaid can't say now
    (the sync itself never fails over what is only shown, never booked)."""
    start = (dt.date.fromisoformat(today) - dt.timedelta(days=PENDING_DAYS)).isoformat()
    out: dict[str, list[dict[str, Any]]] = {}
    offset = 0
    try:
        while True:
            res = api.call(
                "/transactions/get",
                {
                    "access_token": token,
                    "start_date": start,
                    "end_date": today,
                    "options": {
                        "count": 500,
                        "offset": offset,
                        "include_personal_finance_category": True,
                    },
                },
            )
            page = res.get("transactions") or []
            for t in page:
                if t.get("pending"):
                    out.setdefault(t.get("account_id", ""), []).append(_pending_line(t))
            offset += len(page)
            if not page or offset >= int(res.get("total_transactions") or 0):
                break
    except (ConnectorError, KeyError, ValueError, TypeError):
        return None
    return out


def _pending_line(t: dict[str, Any]) -> dict[str, Any]:
    amount = -Decimal(str(t["amount"]))
    pfc = t.get("personal_finance_category") or {}
    primary, detailed = pfc.get("primary") or "", pfc.get("detailed") or ""
    kind = "transfer" if primary in TRANSFERS else ("income" if amount > 0 else "spend")
    return {
        "id": t["transaction_id"],
        "date": t.get("authorized_date") or t["date"],
        "amount": str(amount),
        "description": t.get("original_description") or t.get("name") or "",
        "payee": t.get("merchant_name"),
        "category_hint": detailed or primary or None,
        "kind": kind,
        "hold": kind == "spend" and detailed in HOLDS,
    }
