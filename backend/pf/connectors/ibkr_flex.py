"""Interactive Brokers Flex Web Service: a saved Flex Query, fetched with a token.

Set up once in Client Portal (see docs/connect-accounts.md): an Activity Flex Query in XML with
Account Information, Cash Transactions, Net Asset Value (NAV) in Base and Open Positions, period
"Last 365 Calendar Days"; then Flex Web Service → token. Each sync:

  SendRequest?t=<token>&q=<query id>&v=3  → reference code
  GetStatement?t=<token>&q=<reference>&v=3 → the statement (1019 = still generating: poll)

Booked like the file importers (pf/importers/brokers.py): cash crossing the account's edge
(deposits / withdrawals as transfers, dividends, interest, fees, withholding tax) plus the daily
net asset value as valuation marks — so returns come from NAV and flows.
"""

from __future__ import annotations

import datetime as dt
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from pf.connectors.base import AccountStatement, ConnectorError, Field
from pf.i18n import tr
from pf.importers.brokers import SKIP, TRANSFER, classify

BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
PENDING = {"1019", "1018", "1009"}  # generating / too many requests / server busy: try again
REAUTH = {"1012", "1015"}  # token expired / invalid


def _date(s: str | None) -> dt.date | None:
    s = (s or "").strip().split(";")[0].split(",")[0].split(" ")[0]
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _dec(s: str | None) -> Decimal | None:
    try:
        return Decimal((s or "").replace(",", "")) if s not in (None, "", "--") else None
    except InvalidOperation:
        return None


class IbkrFlex:
    source = "ibkr_flex"
    label = "Interactive Brokers (Flex)"
    fields = (
        Field("token", "Flex Web Service token"),
        Field("query_id", "Flex Query ID", secret=False),
    )

    def __init__(self, sleep: Callable[[float], None] = time.sleep, max_wait: float = 120) -> None:
        self.sleep = sleep
        self.max_wait = max_wait

    def _xml(self, resp: httpx.Response) -> ET.Element:
        resp.raise_for_status()
        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise ConnectorError("IBKR returned something that is not XML") from exc

    def _check(self, root: ET.Element) -> None:
        if root.tag != "FlexStatementResponse" or root.findtext("Status") == "Success":
            return
        code = root.findtext("ErrorCode") or ""
        msg = root.findtext("ErrorMessage") or "Flex request failed"
        if code in PENDING:
            raise _Pending(msg)
        raise ConnectorError(f"IBKR {code}: {msg}", reauth=code in REAUTH)

    def fetch(
        self, client: httpx.Client, secrets: dict[str, Any], state: dict[str, Any]
    ) -> list[AccountStatement]:
        token, query = secrets["token"].strip(), secrets["query_id"].strip()
        root = self._xml(
            client.get(f"{BASE}/SendRequest", params={"t": token, "q": query, "v": "3"})
        )
        self._check(root)
        ref = root.findtext("ReferenceCode")
        url = root.findtext("Url") or f"{BASE}/GetStatement"
        waited = 0.0
        while True:
            stmt = self._xml(client.get(url, params={"t": token, "q": ref, "v": "3"}))
            try:
                self._check(stmt)
                break
            except _Pending:
                if waited >= self.max_wait:
                    raise ConnectorError(
                        "IBKR is still generating the statement; try again later"
                    ) from None
                self.sleep(5)
                waited += 5
        return [self._account(fs) for fs in stmt.iter("FlexStatement")]

    def _account(self, fs: ET.Element) -> AccountStatement:
        if fs.find("CashTransactions") is None and fs.find("EquitySummaryInBase") is None:
            raise ConnectorError(
                tr(
                    "Flex Query 只有账户信息，没有交易或市值。请在 IBKR 的 Activity Flex Query 中添加 Cash Transactions 和 Net Asset Value (NAV) in Base，保存后再同步。"
                )
            )
        account_id = fs.get("accountId", "")
        info = fs.find("AccountInformation")
        base = (info.get("currency") if info is not None else None) or "USD"
        out = AccountStatement(
            source=self.source,
            account_id=account_id,
            institution="IBKR",
            type="Assets",
            currency=base,
            name=(info.get("accountType") if info is not None else None) or "Brokerage",
            mask=account_id[-4:] or None,
            asset_class="brokerage",
        )
        for nav in fs.iter("EquitySummaryByReportDateInBase"):
            d, total = _date(nav.get("reportDate")), _dec(nav.get("total"))
            if d and total is not None:
                out.valuations.append({"date": d.isoformat(), "value": str(total)})
        for ct in fs.iter("CashTransaction"):
            if ct.get("levelOfDetail", "DETAIL").upper() not in ("DETAIL", ""):
                continue
            d = _date(ct.get("dateTime") or ct.get("settleDate") or ct.get("reportDate"))
            amount = _dec(ct.get("amount"))
            if d is None or amount is None or amount == 0:
                continue
            if ct.get("currency", base) != base:
                rate = _dec(ct.get("fxRateToBase"))
                if rate is None:
                    continue
                amount *= rate
            kind = classify(ct.get("type", ""), None, ct.get("symbol", ""))
            if kind == SKIP:
                continue
            desc = ct.get("description") or ct.get("type") or ""
            out.transactions.append(
                {
                    "id": ct.get("transactionID") or None,
                    "date": d.isoformat(),
                    "amount": str(amount.quantize(Decimal("0.01"))),
                    "description": f"{ct.get('type', '')} · {desc}" if kind != TRANSFER else desc,
                    "category": None if kind in (TRANSFER, "unknown") else kind,
                }
            )
        for pos in fs.iter("OpenPosition"):
            if pos.get("levelOfDetail", "SUMMARY").upper() not in ("SUMMARY", ""):
                continue
            out.holdings.append(
                {
                    "symbol": pos.get("symbol"),
                    "quantity": pos.get("position"),
                    "price": pos.get("markPrice"),
                    "value": pos.get("positionValue"),
                    "cost_basis": pos.get("costBasisMoney"),
                    "currency": pos.get("currency"),
                }
            )
        return out


class _Pending(Exception):
    pass
