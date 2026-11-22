"""Manual assertions share the same opening-balance derivation as statement imports."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Connection, text

from pf.i18n import tr
from pf.ledger.core import ensure_account
from pf.ledger.importing import refresh_opening_balance
from pf.ledger.reconcile import Discrepancy, reconcile
from pf.money import Commodities, to_minor


def assert_balance(
    conn: Connection, account: str, date: dt.date, amount: Decimal, commodity: str
) -> list[Discrepancy]:
    if not account.startswith(("Assets:", "Liabilities:")):
        raise ValueError(tr("余额只能记录到资产或负债账户"))
    prec = Commodities(conn).precision(commodity, "currency")
    acc_id = ensure_account(conn, account, currency=commodity)
    conn.execute(
        text("""INSERT INTO balance_assertions(account_id, date, amount, commodity)
        VALUES (:a, :d, :m, :c)"""),
        {"a": acc_id, "d": date.isoformat(), "m": to_minor(amount, prec), "c": commodity},
    )
    refresh_opening_balance(conn, account)
    return reconcile(conn, account)
