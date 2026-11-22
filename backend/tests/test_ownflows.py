"""Your own money moving between your accounts is a transfer, never spending: card-funded wallet
payments, wallet top-ups, transfers to your other wallet login, C2C crypto purchases."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Connection, text

from pf.ledger.categorize import confirm
from pf.ledger.core import (
    UNCATEGORIZED_EXPENSE,
    UNCATEGORIZED_INCOME,
    NewPosting,
    NewTxn,
    ensure_account,
    post,
)
from pf.ledger.ownflows import _is_you, match_own_flows
from pf.reports.cashflow import cashflow

ALIPAY = "Assets:Alipay:1234"
ALIPAY2 = "Assets:Alipay:5678"
CMB = "Assets:CMB:Debit"
CARD = "Liabilities:CMB:Credit"
OKX = "Assets:OKX:Crypto"
DAY = dt.date(2026, 9, 10)

_n = 0


def _account(conn: Connection, name: str, ccy: str) -> None:
    wallet = name.startswith(("Assets:Alipay", "Assets:WeChat"))
    ensure_account(
        conn,
        name,
        currency=ccy,
        institution=name.split(":")[1],
        asset_class="crypto" if "OKX" in name else ("cash" if name.startswith("Assets") else None),
        flow_only=wallet,
    )


def _covered(conn: Connection, account: str, start: dt.date, end: dt.date) -> None:
    """A statement for `account` covering start..end (what makes a wallet 'known' for a day)."""
    global _n
    _n += 1
    acc = conn.execute(text("SELECT id FROM accounts WHERE name = :n"), {"n": account}).scalar()
    conn.execute(
        text(
            "INSERT INTO import_files(sha256, filename, importer, account_id, raw_path,"
            " period_start, period_end) VALUES (:h, 'f', 'test', :a, 'x', :s, :e)"
        ),
        {"h": f"sha{_n}", "a": acc, "s": start.isoformat(), "e": end.isoformat()},
    )


def _line(
    conn: Connection,
    account: str,
    amount: str,
    desc: str,
    *,
    day: dt.date = DAY,
    ccy: str = "CNY",
    note: str | None = None,
    payee: str | None = None,
) -> int:
    """One imported statement line, uncategorised, as the importer posts it."""
    global _n
    _n += 1
    _account(conn, account, ccy)
    a = Decimal(amount)
    return post(
        conn,
        NewTxn(
            date=day,
            description=desc,
            payee=payee,
            note=note,
            weight_commodity=ccy,
            postings=[
                NewPosting(account, a, ccy, source_key=f"k{_n}"),
                NewPosting(UNCATEGORIZED_EXPENSE if a < 0 else UNCATEGORIZED_INCOME, -a, ccy),
            ],
        ),
    )


def _txn(conn: Connection, account: str) -> tuple[str, str, str]:
    """(kind, status, the other account) of the latest line on `account`."""
    r = conn.execute(
        text(
            "SELECT t.kind, t.status, oa.name AS other FROM postings p"
            " JOIN transactions t ON t.id = p.txn_id JOIN accounts a ON a.id = p.account_id"
            " JOIN postings o ON o.txn_id = t.id AND o.id != p.id"
            " JOIN accounts oa ON oa.id = o.account_id WHERE a.name = :n ORDER BY t.id DESC LIMIT 1"
        ),
        {"n": account},
    ).one()
    return r.kind, r.status, r.other


def _spent(conn: Connection) -> int:
    conn.execute(
        text("INSERT OR IGNORE INTO prices VALUES ('2026-09-01', 'USD', 'CNY', '7.1', 'test')")
    )
    return cashflow(conn, "2026-09", "2026-09")[0].expenses


def test_card_funded_wallet_payment_counts_once(conn: Connection) -> None:
    _line(conn, ALIPAY, "-128.50", "晚饭", note="招商银行储蓄卡(8888)", payee="示例餐厅")
    _line(conn, CMB, "-128.50", "支付宝-示例餐厅", day=DAY + dt.timedelta(days=1))
    before = _spent(conn)
    assert match_own_flows(conn) == 1
    assert _txn(conn, CMB) == ("transfer", "confirmed", ALIPAY)  # card -> wallet
    kind = conn.execute(text("SELECT kind FROM transactions WHERE description = '晚饭'")).scalar()
    assert kind == "normal"  # the wallet keeps the purchase
    assert _spent(conn) == before // 2  # counted once, not twice


def test_wallet_balance_payment_leaves_the_card_alone(conn: Connection) -> None:
    _line(conn, ALIPAY, "-30.00", "咖啡", note="余额")
    _line(conn, CARD, "-30.00", "Starbucks")  # same amount, but not through the wallet
    assert match_own_flows(conn) == 0
    assert _txn(conn, CARD)[0] == "normal"


def test_top_up_while_the_wallet_statement_covers_the_day(conn: Connection) -> None:
    _account(conn, ALIPAY, "CNY")
    _covered(conn, ALIPAY, dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    _line(conn, CMB, "-500.00", "支付宝充值")
    _line(conn, CMB, "-80.00", "支付宝-外卖", day=dt.date(2026, 10, 3))  # no wallet statement then
    assert match_own_flows(conn) == 1
    rows = conn.execute(
        text(
            "SELECT t.date, t.kind, t.status FROM transactions t JOIN postings p ON p.txn_id = t.id"
            " JOIN accounts a ON a.id = p.account_id WHERE a.name = :n ORDER BY t.date"
        ),
        {"n": CMB},
    ).all()
    assert [(r.kind, r.status) for r in rows] == [("transfer", "confirmed"), ("normal", "pending")]


def test_transfer_to_your_other_login(conn: Connection) -> None:
    conn.execute(text("INSERT INTO owner_names(name, source) VALUES ('李明', 'statement')"))
    _account(conn, ALIPAY2, "CNY")
    _line(conn, ALIPAY, "-1000.00", "转账", payee="李*")  # masked, still you
    _line(conn, ALIPAY, "-50.00", "转账", payee="王五")  # someone else
    assert match_own_flows(conn) == 1
    kinds = conn.execute(
        text(
            "SELECT t.payee, t.kind FROM transactions t JOIN postings p ON p.txn_id = t.id"
            " JOIN accounts a ON a.id = p.account_id WHERE a.name = :n ORDER BY t.id"
        ),
        {"n": ALIPAY},
    ).all()
    assert [k.kind for k in kinds] == ["transfer", "normal"]
    assert _txn(conn, ALIPAY2)[2] == ALIPAY


def test_masked_names() -> None:
    assert _is_you("李明", ["李明"]) and _is_you("*明", ["李明"]) and _is_you("李*", ["李明"])
    assert (
        not _is_you("李*", ["李小明"])
        and not _is_you("王*", ["李明"])
        and not _is_you(None, ["李明"])
    )
    # Alipay's nickname(real name): the masked real name or the nickname may be yours.
    assert _is_you("欧(**远)", ["宋致远"]) and _is_you("欧（**远）", ["宋致远"])
    assert _is_you("欧(**远)", ["欧"]) and not _is_you("米酒(**春)", ["宋致远"])
    assert not _is_you("邱子柔(邱子柔)", ["宋致远"])


def test_c2c_purchase_becomes_one_transfer_in_two_currencies(conn: Connection) -> None:
    conn.execute(text("INSERT INTO prices VALUES ('2026-09-01', 'USD', 'CNY', '7.1', 'test')"))
    _line(conn, ALIPAY2, "-7250.00", "转账", payee="C2C 商家")
    _line(conn, OKX, "1000.00", "OKX C2C buy USDT", ccy="USD")
    _line(conn, ALIPAY2, "-7250.00", "转账", payee="朋友", day=DAY + dt.timedelta(days=9))
    assert match_own_flows(conn) == 1
    t = conn.execute(
        text(
            "SELECT t.id, t.kind, t.status, t.weight_commodity FROM transactions t"
            " JOIN postings p ON p.txn_id = t.id JOIN accounts a ON a.id = p.account_id"
            " WHERE a.name = :n"
        ),
        {"n": OKX},
    ).one()
    assert (t.kind, t.status, t.weight_commodity) == ("transfer", "pending", "USD")
    legs = conn.execute(
        text(
            "SELECT a.name, p.amount, p.commodity FROM postings p JOIN accounts a"
            " ON a.id = p.account_id WHERE p.txn_id = :t ORDER BY p.id"
        ),
        {"t": t.id},
    ).all()
    assert [(x.name, x.amount, x.commodity) for x in legs] == [
        (ALIPAY2, -725000, "CNY"),
        (OKX, 100000, "USD"),
    ]
    # Confirmed by you, it survives: re-running finds nothing new to change.
    confirm(conn, [t.id])
    assert match_own_flows(conn) == 0


def test_c2c_needs_the_amounts_to_agree(conn: Connection) -> None:
    conn.execute(text("INSERT INTO prices VALUES ('2026-09-01', 'USD', 'CNY', '7.1', 'test')"))
    _line(conn, ALIPAY, "-3000.00", "转账", payee="C2C 商家")  # ~$423, not $1000
    _line(conn, OKX, "1000.00", "OKX C2C buy USDT", ccy="USD")
    assert match_own_flows(conn) == 0


def test_names_you_add_are_kept_and_exported(conn: Connection) -> None:
    from pf.ledger.decisions import export_decisions, import_decisions

    conn.execute(text("INSERT INTO owner_names(name, source) VALUES ('示例用户', 'statement')"))
    conn.execute(text("INSERT INTO owner_names(name, source) VALUES ('李明', 'user')"))
    assert export_decisions(conn)["owner_names"] == ["李明"]  # statement names come from raw/
    conn.execute(text("DELETE FROM owner_names"))
    import_decisions(conn, {"owner_names": ["李明"]})
    assert conn.execute(text("SELECT source FROM owner_names")).scalar() == "user"
