"""OKX with a READ-ONLY API key (never enable Trade or Withdraw on it).

Same model as the file importer (pf/importers/okx.py): the exchange is one account valued in
USD. Each sync takes the total valuation across OKX's accounts (/asset/asset-valuation) as the
day's mark, and stablecoin deposits / withdrawals at $1 as money crossing the edge (left
uncategorised so they pair with the bank or wallet side). Other coins' deposits and trades move
value that the next mark carries.

Requests are signed as OKX documents: base64(HMAC-SHA256(secret, timestamp + method + path +
body)) in OK-ACCESS-SIGN, with OK-ACCESS-KEY / -TIMESTAMP / -PASSPHRASE.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from pf.connectors.base import AccountStatement, ConnectorError, Field
from pf.importers.brokers import FEES
from pf.importers.okx import STABLE

HOSTS = {"global": "https://www.okx.com", "us": "https://app.okx.com", "eea": "https://my.okx.com"}
REAUTH = {"50111", "50113", "50119", "50100", "50101"}  # invalid key / sign / passphrase / frozen
FIRST_SYNC_DAYS = 365


def sign(secret: str, ts: str, method: str, path: str, body: str = "") -> str:
    digest = hmac.new(
        secret.encode(), f"{ts}{method}{path}{body}".encode(), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode()


class OkxApi:
    def __init__(self, client: httpx.Client, secrets: dict[str, Any]) -> None:
        self.client = client
        self.host = HOSTS.get((secrets.get("region") or "global").lower(), HOSTS["global"])
        self.key, self.secret, self.passphrase = (
            secrets["api_key"],
            secrets["secret_key"],
            secrets["passphrase"],
        )

    def get(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        full = f"/api/v5{path}" + (f"?{urlencode(params)}" if params else "")
        ts = (
            dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{dt.datetime.now(dt.UTC).microsecond // 1000:03d}Z"
        )
        headers = {
            "OK-ACCESS-KEY": self.key,
            "OK-ACCESS-SIGN": sign(self.secret, ts, "GET", full),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }
        resp = self.client.get(f"{self.host}{full}", headers=headers)
        data = resp.json() if resp.content else {}
        code = str(data.get("code", resp.status_code))
        if resp.status_code >= 400 or code != "0":
            raise ConnectorError(
                f"OKX {code}: {data.get('msg') or resp.reason_phrase}", reauth=code in REAUTH
            )
        return list(data.get("data") or [])


def _usd(amount: Decimal) -> Decimal:
    """Stablecoin amounts carry up to 8 decimals; the ledger books this account in dollars."""
    return amount.quantize(Decimal("0.01"))


def _when(ms: str) -> dt.date:
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.UTC).date()


class Okx:
    source = "okx"
    label = "OKX (read-only API)"
    fields = (
        Field("api_key", "API Key", secret=False),
        Field("secret_key", "Secret Key"),
        Field("passphrase", "Passphrase"),
        Field("region", "Region: global / us / eea", secret=False),
    )

    def _history(self, api: OkxApi, path: str, since: dt.date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        params: dict[str, str] = {"limit": "100"}
        while True:
            page = api.get(path, params)
            rows += page
            if len(page) < 100 or _when(page[-1]["ts"]) < since:
                return rows
            params = {"limit": "100", "after": page[-1]["ts"]}  # older than the last one

    def fetch(
        self, client: httpx.Client, secrets: dict[str, Any], state: dict[str, Any]
    ) -> list[AccountStatement]:
        api = OkxApi(client, secrets)
        today = dt.date.today()
        since = (
            dt.date.fromisoformat(state["last"]) - dt.timedelta(days=7)
            if state.get("last")
            else today - dt.timedelta(days=FIRST_SYNC_DAYS)
        )
        st = AccountStatement(
            source=self.source,
            account_id="okx",
            institution="OKX",
            type="Assets",
            currency="USD",
            name="Exchange",
            asset_class="crypto",
        )
        val = api.get("/asset/asset-valuation", {"ccy": "USD"})
        if val and val[0].get("totalBal") not in (None, ""):
            st.valuations.append(
                {"date": today.isoformat(), "value": str(_usd(Decimal(val[0]["totalBal"])))}
            )
        for d in self._history(api, "/asset/deposit-history", since):
            if (
                d.get("state") != "2"
                or d.get("ccy", "").upper() not in STABLE
                or _when(d["ts"]) < since
            ):
                continue
            st.transactions.append(
                {
                    "id": f"dep-{d['depId']}",
                    "date": _when(d["ts"]).isoformat(),
                    "amount": str(_usd(Decimal(d["amt"]))),
                    "description": f"Deposit {d['amt']} {d['ccy']}",
                }
            )
        for w in self._history(api, "/asset/withdrawal-history", since):
            if (
                w.get("state") != "2"
                or w.get("ccy", "").upper() not in STABLE
                or _when(w["ts"]) < since
            ):
                continue
            day = _when(w["ts"]).isoformat()
            st.transactions.append(
                {
                    "id": f"wd-{w['wdId']}",
                    "date": day,
                    "amount": str(-_usd(Decimal(w["amt"]))),
                    "description": f"Withdrawal {w['amt']} {w['ccy']}",
                }
            )
            if _usd(Decimal(w.get("fee") or "0")) > 0:  # a fee under half a cent is not a line
                st.transactions.append(
                    {
                        "id": f"wdfee-{w['wdId']}",
                        "date": day,
                        "amount": str(-_usd(Decimal(w["fee"]))),
                        "description": f"Withdrawal fee {w['ccy']}",
                        "category": FEES,
                    }
                )
        state["last"] = today.isoformat()
        return [st]
