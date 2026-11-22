"""SimpleFIN (https://www.simplefin.org/protocol.html): US banks and cards through the SimpleFIN
Bridge, which the user subscribes to themselves. No developer keys: the user pastes one "setup
token" from the Bridge; Keel claims it once for an access URL (kept in the Keychain) and reads
accounts, balances and posted transactions from then on.

    token (base64 of a claim URL) --POST--> access URL (https://user:pass@host/simplefin)
    GET {access}/accounts?version=2&start-date=<epoch>   ->  {errlist, connections, accounts}

Both shapes of the protocol are read: version 2 (`connections`, `errlist`) and version 1
(`org` inside each account, `errors` as strings). Amounts are already in ledger sign: money into
the account is positive, a card's debt is a negative balance.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from pf.connectors.base import AccountStatement, ConnectorError, FetchResult, Field, Secrets
from pf.i18n import tr

FIRST_SYNC_DAYS = 60  # the Bridge serves about this much history in one request
OVERLAP_DAYS = 10  # later syncs re-read this much: late-posting lines, skipped by their ids
_DEBT = re.compile(r"credit|card|visa|mastercard|amex|loan|mortgage|line of credit|heloc", re.I)
_INVEST = re.compile(r"brokerage|invest|ira\b|401|403b|roth|retire|hsa\b|529", re.I)
_RETIRE = re.compile(r"ira\b|401|403b|roth|retire", re.I)


def claim(client: httpx.Client, token: str) -> str:
    """Exchange a setup token for the access URL. A token works once."""
    try:
        url = base64.b64decode("".join(token.split()), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ConnectorError(
            tr("这不是 SimpleFIN 的 Setup Token，请重新复制。"), reauth=True
        ) from exc
    if not url.startswith("https://"):
        raise ConnectorError(tr("这不是 SimpleFIN 的 Setup Token，请重新复制。"), reauth=True)
    resp = client.post(url, headers={"Content-Length": "0"})
    if resp.status_code == 403:
        raise ConnectorError(
            tr(
                "这个 Setup Token 已经用过或已失效。请在 SimpleFIN Bridge 里新建一个；如果不是你用掉的，请先把旧的停用。"
            ),
            reauth=True,
        )
    resp.raise_for_status()
    access = resp.text.strip()
    if not access.startswith("https://"):
        raise ConnectorError("SimpleFIN did not return an access URL")
    return access


def _split(access_url: str) -> tuple[str, tuple[str, str]]:
    """'https://user:pass@host/simplefin' -> ('https://host/simplefin', (user, pass))."""
    u = urlsplit(access_url)
    host = u.hostname or ""
    if u.port:
        host += f":{u.port}"
    return urlunsplit((u.scheme, host, u.path.rstrip("/"), "", "")), (
        u.username or "",
        u.password or "",
    )


def _day(epoch: Any) -> dt.date | None:
    try:
        n = int(epoch)
    except (TypeError, ValueError):
        return None
    return dt.datetime.fromtimestamp(n).date() if n > 0 else None


def _dec(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class SimpleFin:
    source = "simplefin"
    label = "SimpleFIN (US banks and cards)"
    fields = (Field("token", "Setup Token"),)

    def fetch(
        self, client: httpx.Client, secrets: dict[str, Any], state: dict[str, Any]
    ) -> FetchResult:
        if not secrets.get("access_url"):
            secrets = {"access_url": claim(client, str(secrets.get("token") or ""))}
            Secrets.set(self.source, secrets)  # the token is spent; only the access URL is kept
        root, auth = _split(secrets["access_url"])
        today = dt.date.today()
        last = state.get("since")
        start = (
            dt.date.fromisoformat(last) - dt.timedelta(days=OVERLAP_DAYS)
            if last
            else today - dt.timedelta(days=FIRST_SYNC_DAYS)
        )
        resp = client.get(
            f"{root}/accounts",
            params={
                "version": "2",
                "start-date": str(int(dt.datetime.combine(start, dt.time()).timestamp())),
            },
            auth=auth,
        )
        if resp.status_code == 403:
            raise ConnectorError(
                tr(
                    "SimpleFIN 拒绝了访问：授权可能已被撤销。请在 SimpleFIN Bridge 里新建 Setup Token 重新连接。"
                ),
                reauth=True,
            )
        if resp.status_code == 402:
            raise ConnectorError(tr("SimpleFIN Bridge 的订阅已到期，续费后再同步。"))
        resp.raise_for_status()
        data = resp.json()
        warnings = [str(e.get("msg") or e.get("code")) for e in data.get("errlist") or []]
        warnings += [str(e) for e in data.get("errors") or [] if str(e) not in warnings]
        banks = {c.get("conn_id"): c for c in data.get("connections") or []}
        out = []
        for acc in data.get("accounts") or []:
            if (st := self._account(acc, banks, today)) is not None:
                out.append(st)
        state["since"] = today.isoformat()
        return FetchResult(out, warnings)

    def _account(
        self, acc: dict[str, Any], banks: dict[Any, dict[str, Any]], today: dt.date
    ) -> AccountStatement | None:
        currency = str(acc.get("currency") or "USD")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            return None  # a custom currency (miles, points): not money in the ledger
        bank = banks.get(acc.get("conn_id")) or acc.get("org") or {}
        institution = (
            bank.get("org_name")
            or (bank.get("name") or "").split(" - ")[0]
            or bank.get("domain")
            or "Bank"
        )
        name = str(acc.get("name") or "Account")
        balance = _dec(acc.get("balance"))
        holdings = acc.get("holdings") or []
        invest = bool(holdings) or bool(_INVEST.search(name))
        debt = not invest and bool(_DEBT.search(name))
        digits = re.findall(r"\d{3,}", name)
        st = AccountStatement(
            source=self.source,
            account_id=f"{acc.get('conn_id') or bank.get('domain') or ''}:{acc.get('id')}",
            institution=institution,
            type="Liabilities" if debt else "Assets",
            currency=currency,
            name=name,
            mask=digits[-1][-4:] if digits else None,
            asset_class=None
            if debt
            else ("retirement" if _RETIRE.search(name) else "brokerage")
            if invest
            else "cash",
        )
        as_of = _day(acc.get("balance-date")) or today
        if balance is not None:
            if invest:
                st.valuations.append({"date": as_of.isoformat(), "value": str(balance)})
            else:
                st.balances.append({"date": as_of.isoformat(), "amount": str(balance)})
        if invest:
            # An investment account is booked by its value; its lines are trades, not cash flow.
            st.holdings_as_of = as_of.isoformat()
            for h in holdings:
                qty, value = _dec(h.get("shares")), _dec(h.get("market_value"))
                if not h.get("symbol") or qty is None:
                    continue
                st.holdings.append(
                    {
                        "symbol": h["symbol"],
                        "quantity": str(qty),
                        "value": str(value) if value is not None else None,
                        "cost_basis": str(c)
                        if (c := _dec(h.get("cost_basis"))) is not None
                        else None,
                        "cost_basis_per_unit": False,
                        "currency": h.get("currency")
                        if re.fullmatch(r"[A-Z]{3}", str(h.get("currency") or ""))
                        else currency,
                    }
                )
            return st
        for t in acc.get("transactions") or []:
            day, amount = _day(t.get("posted")), _dec(t.get("amount"))
            if t.get("pending") or day is None or amount is None or amount == 0 or not t.get("id"):
                continue  # pending lines change before they post; they arrive again once posted
            st.transactions.append(
                {
                    "id": str(t["id"]),
                    "date": day.isoformat(),
                    "amount": str(amount.quantize(Decimal("0.01"))),
                    "description": t.get("description") or "",
                    "payee": t.get("payee") or None,
                    "memo": t.get("memo") or None,
                }
            )
        return st
