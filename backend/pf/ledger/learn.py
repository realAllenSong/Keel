"""Suggest a category from your own history: a small local classifier, no network, no model.

Every line you (or a rule) confirmed on a card or bank account is an example (broker and exchange
lines state what they are, or are transfers, and are left alone). A new line's description and payee are
compared with them as TF-IDF vectors of character trigrams — robust to the card-terminal noise in
bank text ('SQ *BLUE BOTTLE 0423', 'BLUE BOTTLE COFFEE #12') — and the nearest confirmed lines with
the same direction (money out / money in) vote, weighted by similarity. The approach follows
Beancount's smart_importer. A suggestion stays pending for you to accept, and each acceptance
becomes another example.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import Connection, text

from pf.ledger.core import UNCATEGORIZED_EXPENSE, UNCATEGORIZED_INCOME

K = 7  # neighbours that vote
MIN_SIMILARITY = 0.45  # the nearest example must be at least this close
MIN_SHARE = 0.6  # and the winning category must hold this share of the votes
MAX_EXAMPLES = 20_000

_NOISE = re.compile(r"[0-9#*/\\_.,:;'\"()\[\]-]+")


def clean(description: str | None, payee: str | None = None) -> str:
    s = f"{payee or ''} {description or ''}".upper()
    s = _NOISE.sub(" ", s)
    return " ".join(w for w in s.split() if len(w) > 1)


def features(s: str) -> Counter[str]:
    grams: Counter[str] = Counter()
    for word in s.split():
        padded = f" {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
        grams[f"w:{word}"] += 1
    return grams


@dataclass
class Example:
    vector: dict[str, float]
    category: str
    outflow: bool


class Learner:
    def __init__(self, rows: list[tuple[str, str, bool]]) -> None:
        """rows: (cleaned text, category, outflow)."""
        docs = [features(t) for t, _, _ in rows]
        df: Counter[str] = Counter()
        for d in docs:
            df.update(d.keys())
        n = max(1, len(docs))
        self.idf = {g: math.log((1 + n) / (1 + c)) + 1 for g, c in df.items()}
        self.examples = [
            Example(self._vector(d), cat, out) for d, (_, cat, out) in zip(docs, rows, strict=True)
        ]
        self.index: dict[str, list[int]] = defaultdict(list)
        for i, ex in enumerate(self.examples):
            for g in ex.vector:
                self.index[g].append(i)

    def _vector(self, grams: Counter[str]) -> dict[str, float]:
        v = {g: c * self.idf.get(g, 0.0) for g, c in grams.items() if g in self.idf}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {g: x / norm for g, x in v.items()}

    def suggest(self, cleaned: str, outflow: bool) -> str | None:
        q = self._vector(features(cleaned))
        sims: dict[int, float] = defaultdict(float)
        for g, w in q.items():
            for i in self.index.get(g, ()):
                sims[i] += w * self.examples[i].vector[g]
        near = sorted(
            ((s, i) for i, s in sims.items() if self.examples[i].outflow == outflow),
            reverse=True,
        )[:K]
        if not near or near[0][0] < MIN_SIMILARITY:
            return None
        votes: dict[str, float] = defaultdict(float)
        for s, i in near:
            votes[self.examples[i].category] += s
        best, score = max(votes.items(), key=lambda kv: kv[1])
        return best if score / sum(votes.values()) >= MIN_SHARE else None


def learner(conn: Connection) -> Learner | None:
    """Built from your confirmed, categorised lines; None until there are a few."""
    rows = conn.execute(
        text(
            """SELECT t.description, t.payee, c.name AS category, s.amount < 0 AS outflow
               FROM transactions t
               JOIN postings s ON s.txn_id = t.id AND s.source_key IS NOT NULL
               JOIN accounts sa ON sa.id = s.account_id
               JOIN postings q ON q.txn_id = t.id AND q.id != s.id
               JOIN accounts c ON c.id = q.account_id
               WHERE t.status = 'confirmed' AND t.kind = 'normal'
                 AND COALESCE(sa.asset_class, 'cash') = 'cash'  -- cards and bank accounts
                 AND c.type IN ('Expenses', 'Income') AND c.name NOT IN (:ue, :ui)
               ORDER BY t.date DESC LIMIT :n"""
        ),
        {"ue": UNCATEGORIZED_EXPENSE, "ui": UNCATEGORIZED_INCOME, "n": MAX_EXAMPLES},
    ).all()
    if len(rows) < 5:
        return None
    return Learner([(clean(r.description, r.payee), r.category, bool(r.outflow)) for r in rows])
