"""FRED (St. Louis Fed) public CSV download - no API key needed.

Series used:  DTB3 (3-month T-bill, % p.a.) · DEXCHUS (CNY per 1 USD) · CPIAUCSL (CPI)."""

from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal, InvalidOperation

import httpx

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def parse_csv(text: str) -> list[tuple[dt.date, Decimal]]:
    """FRED CSV -> [(date, value)], skipping '.'/blank (missing) observations."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    out: list[tuple[dt.date, Decimal]] = []
    for r in rows[1:]:  # header: observation_date|DATE, <series id>
        if len(r) < 2 or not r[0]:
            continue
        try:
            out.append((dt.date.fromisoformat(r[0]), Decimal(r[1])))
        except (ValueError, InvalidOperation):
            continue
    return out


def fetch(
    series: str, start: dt.date | None = None, timeout: float = 30
) -> list[tuple[dt.date, Decimal]]:
    params = {"id": series}
    if start:
        params["cosd"] = start.isoformat()
    r = httpx.get(URL, params=params, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return parse_csv(r.text)
