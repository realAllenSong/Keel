"""Your own money moving between your own accounts, recognised as transfers so it is never
counted as spending or income. `transfers.match_transfers` pairs the plain case (one side out,
the other side in, same amount and currency); this module covers what that cannot see:

  card-funded wallet payments  Alipay / WeChat pay a merchant with a bank or credit card: the
                               wallet shows the purchase, the card shows the same amount going
                               to 支付宝 / 财付通. Both are outflows, so they never pair as
                               opposites. The wallet line keeps the merchant and the category; the
                               card line becomes card → wallet.
  top-ups and withdrawals      the card line that funds a wallet (or receives from it) while that
                               wallet's statement covers the day: card ↔ wallet.
  your other wallet login      a wallet transfer whose counterparty is you (your name from the
                               statements' header, or one you added): wallet → your other login.
  C2C crypto purchases         a CNY payment to a C2C seller and the stablecoin credited on the
                               exchange the same or next day, worth about the same at that day's
                               rate: wallet → exchange, one transaction in two currencies.

Only lines no decision of yours has fixed are touched (pending, or categorised by the built-in
merchant list), so a rebuild reproduces the same result. Heuristic matches stay pending for you
to confirm; matches the statements state outright are confirmed.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection, text

from pf.ledger.core import NewPosting, NewTxn, delete_txn, ensure_account, post, recategorize
from pf.money import from_minor

FUNDING_DAYS = 3
C2C_DAYS = 1
C2C_TOLERANCE = Decimal("0.05")  # C2C sellers price a few percent off the official rate
CNY_USD_FALLBACK = Decimal("0.14")  # only when the ledger has no CNY rate for the day

WALLETS = {
    "Alipay": re.compile(r"支付宝|alipay|蚂蚁|花呗|余额宝", re.I),
    "WeChat": re.compile(r"财付通|tenpay|微信|wechat|weixin|零钱通", re.I),
}
# Wallet payment methods that are the wallet's own money (not a linked card).
OWN_BALANCE = re.compile(r"余额|零钱|花呗|账户余额|亲情卡|信用购|月付")
CARD_LAST4 = re.compile(r"[(（](\d{4})[)）]")
MOVE_WORDS = re.compile(r"充值|提现|转账|转入|转出|还款|top.?up|withdraw", re.I)
C2C_WORDS = re.compile(r"c2c|p2p|快捷买币|买币|法币|buy crypto", re.I)


@dataclass
class Line:
    txn_id: int
    date: dt.date
    description: str
    payee: str | None
    note: str | None
    account: str
    account_id: int
    institution: str | None
    asset_class: str | None
    flow_only: bool
    account_type: str
    amount: int  # minor units, ledger sign of the statement account
    commodity: str
    precision: int
    source_key: str
    cat_posting_id: int

    @property
    def wallet(self) -> str | None:
        return self.institution if self.institution in WALLETS and self.flow_only else None

    @property
    def text(self) -> str:
        return f"{self.description} {self.payee or ''} {self.note or ''}"


# Imported 2-leg lines no decision of yours has fixed: still pending, or categorised only by the
# built-in merchant list (a wallet statement imported later can still claim them).
_OPEN = """
SELECT t.id AS txn_id, t.date, t.description, t.payee, t.note,
       a.name AS account, a.id AS account_id, a.institution, a.asset_class, a.flow_only,
       a.type AS account_type, p.amount, p.commodity, c.precision, p.source_key,
       q.id AS cat_posting_id
FROM transactions t
JOIN postings p ON p.txn_id = t.id AND p.source_key IS NOT NULL
JOIN accounts a ON a.id = p.account_id AND a.type IN ('Assets', 'Liabilities')
JOIN commodities c ON c.code = p.commodity
JOIN postings q ON q.txn_id = t.id AND q.id != p.id
JOIN accounts qa ON qa.id = q.account_id AND qa.type IN ('Expenses', 'Income')
WHERE t.sealed = 1 AND t.kind = 'normal'
  AND (t.status = 'pending' OR t.category_source IN ('seed', 'hint', 'learned'))
  AND (SELECT COUNT(*) FROM postings WHERE txn_id = t.id) = 2
  -- a category you chose for this exact line stands; a transfer you confirmed is re-paired
  AND NOT EXISTS (SELECT 1 FROM overrides o WHERE o.account_name = a.name
                  AND o.source_key = p.source_key
                  AND (o.target_account LIKE 'Expenses:%' OR o.target_account LIKE 'Income:%'))
ORDER BY t.date, p.source_key
"""


def _open_lines(conn: Connection) -> list[Line]:
    return [
        Line(
            txn_id=r.txn_id,
            date=dt.date.fromisoformat(r.date),
            description=r.description,
            payee=r.payee,
            note=r.note,
            account=r.account,
            account_id=r.account_id,
            institution=r.institution,
            asset_class=r.asset_class,
            flow_only=bool(r.flow_only),
            account_type=r.account_type,
            amount=r.amount,
            commodity=r.commodity,
            precision=r.precision,
            source_key=r.source_key,
            cat_posting_id=r.cat_posting_id,
        )
        for r in conn.execute(text(_OPEN))
    ]


def match_own_flows(conn: Connection) -> int:
    """Turn every recognisable movement of your own money into a transfer. Returns how many."""
    lines = _open_lines(conn)
    used: set[int] = set()
    n = _c2c(conn, lines, used)
    n += _funded_payments(conn, lines, used)
    n += _own_logins(conn, lines, used)
    n += _wallet_moves(conn, lines, used)
    return n


def _to_transfer(conn: Connection, line: Line, other: str, *, confident: bool, note: str) -> None:
    """Re-point the line's category leg at `other` (an account of yours)."""
    recategorize(conn, line.cat_posting_id, other, payee=f"Transfer → {other}")
    conn.execute(
        text(
            "UPDATE transactions SET kind = 'transfer', status = :st, category_source = 'transfer',"
            " note = trim(COALESCE(note || ' · ', '') || :note) WHERE id = :id"
        ),
        {"st": "confirmed" if confident else "pending", "note": note, "id": line.txn_id},
    )


def _is_card(line: Line) -> bool:
    """A bank account or credit card (not a wallet, not a brokerage or exchange)."""
    if line.wallet:
        return False
    return line.account_type == "Liabilities" or line.asset_class == "cash"


def _wallet_of(line: Line) -> str | None:
    """Which wallet platform a card line names, if any."""
    return next((w for w, pat in WALLETS.items() if pat.search(line.text)), None)


# ---------------------------------------------------------------- card-funded wallet payments
def _funded_payments(conn: Connection, lines: list[Line], used: set[int]) -> int:
    cards = [x for x in lines if _is_card(x) and x.amount < 0]
    n = 0
    for w in lines:
        if w.txn_id in used or not w.wallet or w.amount >= 0:
            continue
        method = w.note or ""
        if OWN_BALANCE.search(method) and not CARD_LAST4.search(method):
            continue  # paid from the wallet's own balance: no card line to claim
        last4 = m.group(1) if (m := CARD_LAST4.search(method)) else None
        best: tuple[tuple[int, int, str], Line] | None = None
        for c in cards:
            if c.txn_id in used or c.amount != w.amount or c.commodity != w.commodity:
                continue
            gap = abs((c.date - w.date).days)
            if gap > FUNDING_DAYS:
                continue
            named = _wallet_of(c) == w.wallet
            card_match = bool(last4 and last4 in c.account)
            if not (named or card_match):
                continue
            key = (0 if card_match else 1, gap, c.source_key)
            if best is None or key < best[0]:
                best = (key, c)
        if best is None:
            continue
        c = best[1]
        _to_transfer(
            conn, c, w.account, confident=True, note=f"paid through {w.wallet}: {w.description}"
        )
        used.update((c.txn_id, w.txn_id))
        n += 1
    return n


# ---------------------------------------------------------------- top-ups and withdrawals
def _coverage(conn: Connection) -> dict[str, list[tuple[str, dt.date, dt.date]]]:
    """Each wallet platform's accounts and the periods their statements cover."""
    out: dict[str, list[tuple[str, dt.date, dt.date]]] = {}
    for r in conn.execute(
        text(
            "SELECT a.name, a.institution, f.period_start, f.period_end FROM import_files f"
            " JOIN accounts a ON a.id = f.account_id"
            " WHERE a.flow_only = 1 AND f.period_start IS NOT NULL ORDER BY f.id"
        )
    ):
        if r.institution in WALLETS:
            out.setdefault(r.institution, []).append(
                (r.name, dt.date.fromisoformat(r.period_start), dt.date.fromisoformat(r.period_end))
            )
    return out


def _wallet_moves(conn: Connection, lines: list[Line], used: set[int]) -> int:
    cover = _coverage(conn)
    n = 0
    for c in lines:
        if c.txn_id in used or not _is_card(c):
            continue
        platform = _wallet_of(c)
        if platform is None or platform not in cover:
            continue
        accounts = [a for a, s, e in cover[platform] if s <= c.date <= e]
        if not accounts:
            continue  # no statement for that day: the card line is all we know, keep it
        _to_transfer(
            conn,
            c,
            accounts[0],
            confident=bool(MOVE_WORDS.search(c.text)),
            note=f"moved {'to' if c.amount < 0 else 'from'} {platform}",
        )
        used.add(c.txn_id)
        n += 1
    return n


# ---------------------------------------------------------------- your other wallet login
def owner_names(conn: Connection) -> list[str]:
    return [r.name for r in conn.execute(text("SELECT name FROM owner_names ORDER BY name"))]


_NICK_REAL = re.compile(r"^(.*?)[(（]([^()（）]+)[)）]$")


def _is_you(payee: str | None, names: list[str]) -> bool:
    """Exact name, or a masked one (张*, *三, 张**) that fits a name of yours. Alipay shows a
    counterparty as nickname(real name), e.g. 欧(**远): either part may be yours."""
    if not payee:
        return False
    p = payee.strip()
    m = _NICK_REAL.match(p)
    if m:
        return any(_is_you(part, names) for part in m.groups() if part.strip())
    for name in names:
        if p == name:
            return True
        if (
            "*" in p
            and len(p) == len(name)
            and all(a == "*" or a == b for a, b in zip(p, name, strict=True))
        ):
            return True
    return False


def _own_logins(conn: Connection, lines: list[Line], used: set[int]) -> int:
    names = owner_names(conn)
    if not names:
        return 0
    siblings: dict[str, list[str]] = {}
    for r in conn.execute(
        text("SELECT name, institution FROM accounts WHERE flow_only = 1 ORDER BY id")
    ):
        if r.institution in WALLETS:
            siblings.setdefault(r.institution, []).append(r.name)
    n = 0
    for w in lines:
        if w.txn_id in used or not w.wallet or not _is_you(w.payee, names):
            continue
        others = [a for a in siblings.get(w.wallet, []) if a != w.account]
        target = (
            others[0]
            if len(others) == 1
            else f"Assets:{w.wallet}:Other login"  # you, but no statement for that login yet
        )
        ensure_account(
            conn,
            target,
            currency=w.commodity,
            institution=w.wallet,
            asset_class="cash",
            flow_only=True,
        )
        _to_transfer(conn, w, target, confident=len(others) == 1, note="to your other login")
        used.add(w.txn_id)
        n += 1
    return n


# ---------------------------------------------------------------- C2C crypto purchases
def _cny_usd(conn: Connection, day: dt.date) -> Decimal:
    for base, quote, invert in (("CNY", "USD", False), ("USD", "CNY", True)):
        row = conn.execute(
            text(
                "SELECT rate FROM prices WHERE base = :b AND quote = :q AND date <= :d"
                " ORDER BY date DESC LIMIT 1"
            ),
            {"b": base, "q": quote, "d": day.isoformat()},
        ).first()
        if row:
            r = Decimal(row.rate)
            return 1 / r if invert else r
    return CNY_USD_FALLBACK


def _c2c(conn: Connection, lines: list[Line], used: set[int]) -> int:
    buys = [
        x for x in lines if x.asset_class == "crypto" and x.amount > 0 and C2C_WORDS.search(x.text)
    ]
    if not buys:
        return 0
    pays = [x for x in lines if x.commodity == "CNY" and x.amount < 0 and x.asset_class == "cash"]
    n = 0
    for b in buys:
        usd = from_minor(b.amount, b.precision)
        best: tuple[tuple[Decimal, int, str], Line] | None = None
        for p in pays:
            if p.txn_id in used:
                continue
            gap = abs((p.date - b.date).days)
            if gap > C2C_DAYS:
                continue
            worth = -from_minor(p.amount, p.precision) * _cny_usd(conn, p.date)
            off = abs(worth - usd) / usd if usd else Decimal(1)
            if off > C2C_TOLERANCE:
                continue
            key = (off, gap, p.source_key)
            if best is None or key < best[0]:
                best = (key, p)
        if best is None:
            continue
        p = best[1]
        for t in (p, b):
            delete_txn(conn, t.txn_id)
        cny = from_minor(p.amount, p.precision)
        tid = post(
            conn,
            NewTxn(
                date=p.date,
                description=b.description,
                payee=f"Transfer → {b.account}",
                kind="transfer",
                status="pending",  # a match by amount and day: yours to confirm
                weight_commodity=b.commodity,
                note=f"C2C: paid {-cny} CNY ({p.payee or p.description}), got {usd} {b.commodity}",
                postings=[
                    NewPosting(p.account, cny, "CNY", weight=-usd, source_key=p.source_key),
                    NewPosting(b.account, usd, b.commodity, source_key=b.source_key),
                ],
            ),
        )
        conn.execute(
            text("UPDATE transactions SET category_source = 'transfer' WHERE id = :id"), {"id": tid}
        )
        used.update((p.txn_id, b.txn_id))
        n += 1
    return n
