"""Stable per-line keys. The same statement line imported twice (same file or an overlapping
export) yields the same key, which the DB's unique index on (account, source_key) rejects."""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from pf.importers.base import RawTxn

_WS = re.compile(r"\s+")


def normalize(desc: str) -> str:
    return _WS.sub(" ", desc.strip().lower())


def source_keys(rows: list[RawTxn]) -> list[str]:
    """Identical (date, amount, description) lines within one file are distinguished by their
    occurrence index, so two genuine $4.50 coffees on the same day both survive."""
    seen: Counter[tuple[str, str, str, str]] = Counter()
    keys: list[str] = []
    for r in rows:
        base = (
            r.date.isoformat(),
            str(r.amount.normalize()),
            normalize(r.description),
            r.ref or "",
        )
        idx = seen[base]
        seen[base] += 1
        keys.append(hashlib.sha256("|".join((*base, str(idx))).encode()).hexdigest()[:32])
    return keys
