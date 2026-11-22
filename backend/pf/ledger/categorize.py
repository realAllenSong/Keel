"""Categorisation: assign the counter-leg of each imported line to an Expenses/Income account.

Precedence (first hit wins):
  override (your one-off fix for this exact line)   -> confirmed
  user rule                                          -> confirmed
  seed rule (built-in merchant list)                 -> confirmed
  llm rule  (saved LLM answer)                       -> pending (suggested)
  learned   (nearest of your confirmed lines, learn.py) -> confirmed, marked 按你的习惯
  bank hint (e.g. Chase's own category column)       -> confirmed, marked 银行建议
The last two are accepted without asking (97% right on real data; asking about hundreds of lines
helps nobody) but stay marked, changeable, and open to transfer pairing.
Confirmed transactions are never touched again automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

import yaml
from sqlalchemy import Connection, text

from pf.ledger.core import UNCATEGORIZED_EXPENSE, UNCATEGORIZED_INCOME, recategorize
from pf.ledger.dedup import normalize

TRANSFER_MARK = "@transfer"


_SHORT_ASCII = re.compile(r"[a-z0-9]{1,4}")


@dataclass(frozen=True)
class Rule:
    pattern: str
    target: str
    match_type: str = "contains"
    field: str = "description"
    payee: str | None = None
    account_filter: str | None = None
    amount_min: int | None = None
    amount_max: int | None = None
    source: str = "user"
    id: int | None = None

    def matches(self, desc: str, payee: str | None, account: str, amount: int) -> bool:
        if self.account_filter and not account.startswith(self.account_filter):
            return False
        if self.amount_min is not None and amount < self.amount_min:
            return False
        if self.amount_max is not None and amount > self.amount_max:
            return False
        subject = normalize(payee or "") if self.field == "payee" else normalize(desc)
        pat = self.pattern.lower()
        if self.match_type == "exact":
            return subject == normalize(pat)
        if self.match_type == "regex":
            return re.search(self.pattern, subject, re.I) is not None
        if _SHORT_ASCII.fullmatch(pat):
            # A short Latin pattern ("ic", "cl", "tst") must be a whole word: as a bare substring
            # it files AnthropIC under Instacart's "ic" rule.
            return re.search(rf"(?<![a-z0-9]){re.escape(pat)}(?![a-z0-9])", subject) is not None
        return pat in subject


@cache
def _seed() -> tuple[tuple[Rule, ...], dict[str, str]]:
    data = yaml.safe_load(
        resources.files("pf.ledger").joinpath("seed_rules.yaml").read_text(encoding="utf-8")
    )
    rules = tuple(
        Rule(
            pattern=r["pattern"],
            target=r["target"],
            match_type=r.get("match_type", "contains"),
            payee=r.get("payee"),
            source="seed",
        )
        for r in data["rules"]
    )
    return rules, dict(data.get("hints", {}))


def db_rules(conn: Connection, source: str) -> list[Rule]:
    rows = conn.execute(
        text(
            "SELECT id, pattern, target_account, match_type, field, payee, account_filter,"
            " amount_min, amount_max, source FROM rules WHERE source = :s"
            " ORDER BY priority, length(pattern) DESC, id DESC"
        ),
        {"s": source},
    ).all()
    return [
        Rule(
            id=r.id,
            pattern=r.pattern,
            target=r.target_account,
            match_type=r.match_type,
            field=r.field,
            payee=r.payee,
            account_filter=r.account_filter,
            amount_min=r.amount_min,
            amount_max=r.amount_max,
            source=r.source,
        )
        for r in rows
    ]


# Imported 2-leg transactions still open for automatic categorisation.
_OPEN = """
SELECT t.id AS txn_id, t.description, t.payee, t.kind, sa.name AS account, s.amount,
       sa.asset_class,
       s.source_key, q.id AS cat_posting_id, q.memo AS hint
FROM transactions t
JOIN postings s ON s.txn_id = t.id AND s.source_key IS NOT NULL
JOIN accounts sa ON sa.id = s.account_id
JOIN postings q ON q.txn_id = t.id AND q.id != s.id
WHERE t.sealed = 1 AND t.status = 'pending' AND t.kind = 'normal'
  AND (SELECT COUNT(*) FROM postings WHERE txn_id = t.id) = 2
"""


def categorize(conn: Connection, txn_ids: list[int] | None = None) -> int:
    """Categorise pending imported transactions. Returns how many got a category."""
    sql = _OPEN + (
        " AND t.id IN (SELECT value FROM json_each(:ids))" if txn_ids is not None else ""
    )
    import json

    params = {"ids": json.dumps(txn_ids)} if txn_ids is not None else {}
    rows = conn.execute(text(sql), params).all()
    if not rows:
        return 0
    overrides = {
        (r.account_name, r.source_key): (r.target_account, r.payee)
        for r in conn.execute(text("SELECT * FROM overrides"))
    }
    seed_rules, hints = _seed()
    tiers: list[tuple[str, str, list[Rule]]] = [
        ("rule", "confirmed", db_rules(conn, "user")),
        ("seed", "confirmed", list(seed_rules)),
        ("llm", "pending", db_rules(conn, "llm")),
    ]
    from pf.ledger.learn import clean, learner

    model = learner(conn)
    done = 0
    for r in rows:
        decision: tuple[str, str | None, str, str] | None = None  # target, payee, source, status
        if (ov := overrides.get((r.account, r.source_key))) and ov[0] != TRANSFER_MARK:
            decision = (ov[0], ov[1], "override", "confirmed")
        else:
            for source, status, rules in tiers:
                hit = next(
                    (x for x in rules if x.matches(r.description, r.payee, r.account, r.amount)),
                    None,
                )
                if hit:
                    decision = (hit.target, hit.payee, source, status)
                    break
        # Spending accounts only: broker / exchange lines say what they are, or are transfers.
        # Person-to-person transfers ('Transfer', '转账', Zelle) depend on who, not on the text:
        # they wait for the per-person review instead of being guessed from look-alikes.
        if (
            decision is None
            and model is not None
            and r.asset_class in (None, "cash")
            and not is_person_transfer(r.description, r.hint)
        ):
            guess = model.suggest(clean(r.description, r.payee), r.amount < 0)
            decision = (guess, None, "learned", "confirmed") if guess else None
        target = _hint_target(hints, r.hint)
        if decision is None and target and r.amount < 0:
            decision = (target, None, "hint", "confirmed")
        if decision is None:
            continue
        target, payee, source, status = decision
        recategorize(conn, r.cat_posting_id, target, payee=payee)
        # A rule or fix that points at one of your own accounts moves money, it doesn't spend it.
        own = target.startswith(("Assets:", "Liabilities:"))
        conn.execute(
            text(
                "UPDATE transactions SET status = :st, category_source = :src,"
                " kind = CASE WHEN :own THEN 'transfer' ELSE kind END WHERE id = :id"
            ),
            {"st": status, "src": source, "own": own, "id": r.txn_id},
        )
        done += 1
    return done


def apply_transfer_overrides(conn: Connection) -> None:
    """Pending transfers you previously confirmed: persisted as override '@transfer' (a pair) or
    as the account of yours the line moves money to (a card ↔ wallet move)."""
    conn.execute(
        text(
            """
            UPDATE transactions SET status = 'confirmed', category_source = 'override'
            WHERE kind = 'transfer' AND status = 'pending' AND id IN (
                SELECT p.txn_id FROM postings p JOIN accounts a ON a.id = p.account_id
                JOIN overrides o ON o.account_name = a.name AND o.source_key = p.source_key
                WHERE o.target_account = :mark OR o.target_account LIKE 'Assets:%'
                   OR o.target_account LIKE 'Liabilities:%')
            """
        ),
        {"mark": TRANSFER_MARK},
    )


# ---------------------------------------------------------------- user actions
def statement_leg(conn: Connection, txn_id: int):  # type: ignore[no-untyped-def]
    return conn.execute(
        text(
            "SELECT a.name AS account, p.source_key, p.amount FROM postings p"
            " JOIN accounts a ON a.id = p.account_id"
            " WHERE p.txn_id = :t AND p.source_key IS NOT NULL ORDER BY p.id LIMIT 1"
        ),
        {"t": txn_id},
    ).first()


def set_category(
    conn: Connection,
    txn_id: int,
    target: str,
    *,
    payee: str | None = None,
    remember: str = "override",  # override | rule | none
    rule_pattern: str | None = None,
) -> int:
    """You categorise one transaction. Persisted so `rebuild` reproduces it. With remember='rule'
    the decision also becomes a rule and is applied to every other pending match. Returns the
    number of other transactions re-categorised by the new rule."""
    cat = conn.execute(
        text(
            # The category leg: not a statement line and, when both legs were typed by hand,
            # the income/expense one (never the account the money sat in).
            "SELECT q.id FROM postings q JOIN accounts a ON a.id = q.account_id"
            " WHERE q.txn_id = :t AND q.source_key IS NULL"
            " ORDER BY (a.type NOT IN ('Expenses', 'Income')), q.id LIMIT 1"
        ),
        {"t": txn_id},
    ).first()
    if cat is None:
        raise ValueError(f"transaction {txn_id} has no category leg")
    leg = statement_leg(conn, txn_id)
    recategorize(conn, cat.id, target, payee=payee)
    conn.execute(
        text("UPDATE transactions SET status='confirmed', category_source='user' WHERE id=:t"),
        {"t": txn_id},
    )
    if leg is not None and remember in ("override", "rule"):
        conn.execute(
            text(
                "INSERT INTO overrides(account_name, source_key, target_account, payee)"
                " VALUES (:a, :k, :t, :p) ON CONFLICT(account_name, source_key)"
                " DO UPDATE SET target_account = excluded.target_account, payee = excluded.payee"
            ),
            {"a": leg.account, "k": leg.source_key, "t": target, "p": payee},
        )
    if remember != "rule":
        _refresh_if_asset(conn, target)
        return 0
    desc = conn.execute(text("SELECT description FROM transactions WHERE id=:t"), {"t": txn_id})
    pattern = rule_pattern or merchant_key(desc.scalar_one())
    conn.execute(
        text(
            "INSERT INTO rules(pattern, target_account, payee, source) VALUES (:p, :t, :y, 'user')"
        ),
        {"p": pattern, "t": target, "y": payee},
    )
    # Apply retroactively, but only to pending lines this new rule actually matches.
    rule = Rule(pattern=pattern, target=target)
    hits = [
        r.txn_id
        for r in conn.execute(text(_OPEN)).all()
        if rule.matches(r.description, r.payee, r.account, r.amount)
    ]
    n = categorize(conn, hits)
    _refresh_if_asset(conn, target)
    return n


def _refresh_if_asset(conn: Connection, target: str) -> None:
    """Contributions moved into an asset account change what its valuations must adjust."""
    if target.startswith(("Assets:", "Liabilities:")):
        from pf.ledger.valuation import refresh_valuations

        refresh_valuations(conn, target)


def confirm(conn: Connection, txn_ids: list[int]) -> None:
    """Accept the current (suggested) category or transfer pairing; persisted as overrides."""
    for tid in txn_ids:
        row = conn.execute(
            text(
                "SELECT t.kind, a.name AS cat FROM transactions t"
                " JOIN postings q ON q.txn_id = t.id AND q.source_key IS NULL"
                " JOIN accounts a ON a.id = q.account_id WHERE t.id = :t"
            ),
            {"t": tid},
        ).first()
        leg = statement_leg(conn, tid)
        if leg is None:
            continue
        target = TRANSFER_MARK if row is None else row.cat
        if row is not None and row.cat in (UNCATEGORIZED_EXPENSE, UNCATEGORIZED_INCOME):
            continue  # nothing to accept yet
        conn.execute(
            text(
                "INSERT INTO overrides(account_name, source_key, target_account) VALUES (:a,:k,:t)"
                " ON CONFLICT(account_name, source_key)"
                " DO UPDATE SET target_account = excluded.target_account"
            ),
            {"a": leg.account, "k": leg.source_key, "t": target},
        )
        conn.execute(
            text(
                "UPDATE transactions SET status='confirmed',"
                " category_source = COALESCE(category_source, 'override') WHERE id=:t"
            ),
            {"t": tid},
        )


_NOISE = re.compile(r"[#*]?\b[\w-]*\d{3,}[\w-]*\b|\s+(ppd|web|ccd)\s+id:?.*$", re.I)


def merchant_key(description: str) -> str:
    """'WHOLEFDS PRN 10234' -> 'wholefds prn'; 'AMAZON.COM*AB12CD' -> 'amazon.com'."""
    s = normalize(description).split("*", 1)[0]
    s = _NOISE.sub(" ", s)
    tokens = [t for t in s.split() if t]
    return " ".join(tokens[:3]) or normalize(description)


def _hint_target(hints: dict[str, str], hint: str | None) -> str | None:
    """A bank's own category, most specific first: 'Restaurant-Bar & Café' falls back to
    'Restaurant'; Plaid's 'FOOD_AND_DRINK_GROCERIES' to 'FOOD_AND_DRINK'."""
    if not hint:
        return None
    candidates = [hint, hint.split("-")[0].strip()]
    parts = hint.split("_")
    candidates += ["_".join(parts[:n]) for n in range(len(parts) - 1, 0, -1)]
    return next((hints[c] for c in candidates if c in hints), None)


_P2P_TEXT = re.compile(
    r"^\s*(transfer|转账|微信转账|转账备注.*|红包|微信红包|收钱码收款|二维码收款)\s*$"
    r"|\b(zelle|venmo|cash app)\b",
    re.I,
)
_P2P_HINTS = {"转账红包", "转账", "微信红包", "亲友代付"}


def is_person_transfer(description: str, hint: str | None = None) -> bool:
    """Money to or from a person, where the text says nothing about what it was for."""
    return bool(_P2P_TEXT.search(description or "")) or (hint or "").strip() in _P2P_HINTS
