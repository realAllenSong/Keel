"""Daily market refresh: FX rates into the ledger's `prices` table, macro series to Parquet.

Failures are reported, never fatal: the ledger keeps working on the last cached data."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, text

from pf.market.providers import fred
from pf.settings import get_settings

FX_SERIES = {"CNY": "DEXCHUS"}  # FRED quotes CNY per USD
# Other currencies a holding or an account can be in. Reports stay in USD or CNY; these rates
# only turn a Hong Kong stock or a euro balance into them. Fetched once such a currency appears.
# (series, quoted per USD): FRED gives EUR, GBP, AUD and NZD the other way round, as USD per unit.
OTHER_FX: dict[str, tuple[str, bool]] = {
    "HKD": ("DEXHKUS", True),
    "JPY": ("DEXJPUS", True),
    "CAD": ("DEXCAUS", True),
    "CHF": ("DEXSZUS", True),
    "SGD": ("DEXSIUS", True),
    "KRW": ("DEXKOUS", True),
    "TWD": ("DEXTAUS", True),
    "INR": ("DEXINUS", True),
    "MXN": ("DEXMXUS", True),
    "EUR": ("DEXUSEU", False),
    "GBP": ("DEXUSUK", False),
    "AUD": ("DEXUSAL", False),
    "NZD": ("DEXUSNZ", False),
}
MACRO_SERIES = ("DTB3", "CPIAUCSL")


@dataclass
class MarketUpdate:
    fx_rows: int = 0
    macro: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def store_fx(
    conn: Connection, commodity: str, obs: list[tuple[dt.date, Decimal]], per_usd: bool = True
) -> int:
    """Store as 1 <commodity> = rate USD (inverting FRED's quote when it is per USD)."""
    rows = [
        {
            "d": d.isoformat(),
            "b": commodity,
            "q": "USD",
            "r": str(((1 / v) if per_usd else v).quantize(Decimal("1e-8"))),
            "s": "fred",
        }
        for d, v in obs
        if v > 0
    ]
    if rows:
        conn.execute(
            text(
                "INSERT INTO prices(date, base, quote, rate, source) VALUES (:d, :b, :q, :r, :s)"
                " ON CONFLICT(date, base, quote) DO UPDATE SET rate = excluded.rate, source = excluded.source"
            ),
            rows,
        )
    return len(rows)


def currencies_in_use(conn: Connection) -> set[str]:
    """Every currency a position, a trade, an account or a posting is in."""
    rows = conn.execute(
        text(
            "SELECT currency AS c FROM holdings UNION SELECT currency FROM trades"
            " UNION SELECT currency FROM accounts WHERE currency IS NOT NULL"
            " UNION SELECT commodity FROM postings"
        )
    )
    return {r.c for r in rows if r.c}


def macro_path(series: str) -> Path:
    return get_settings().market_dir / f"fred_{series}.parquet"


def store_macro(series: str, obs: list[tuple[dt.date, Decimal]]) -> int:
    p = macro_path(series)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame({"date": [d for d, _ in obs], "value": [float(v) for _, v in obs]})
    if p.exists():
        new = pd.concat([pd.read_parquet(p), new]).drop_duplicates("date", keep="last")
    new.sort_values("date").to_parquet(p, index=False)
    return len(obs)


def latest_macro(series: str) -> tuple[dt.date, float] | None:
    p = macro_path(series)
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if df.empty:
        return None
    last = df.dropna().sort_values("date").iloc[-1]
    return dt.date.fromisoformat(str(last["date"])[:10]), float(last["value"])


def update_market(conn: Connection, fetch=fred.fetch) -> MarketUpdate:  # type: ignore[no-untyped-def]
    res = MarketUpdate()
    wanted = {c: (series, True) for c, series in FX_SERIES.items()}
    wanted |= {c: OTHER_FX[c] for c in currencies_in_use(conn) if c in OTHER_FX}
    for commodity, (series, per_usd) in wanted.items():
        last = conn.execute(
            text(
                "SELECT MAX(date) FROM prices WHERE base = :b AND quote = 'USD' AND source = 'fred'"
            ),
            {"b": commodity},
        ).scalar()
        first = dt.date(2000, 1, 1) if commodity in FX_SERIES else dt.date(2015, 1, 1)
        start = dt.date.fromisoformat(last) - dt.timedelta(days=7) if last else first
        try:
            res.fx_rows += store_fx(conn, commodity, fetch(series, start), per_usd)
        except Exception as e:  # network, HTTP, parsing - keep going
            res.errors.append(f"{series}: {e}")
    for series in MACRO_SERIES:
        try:
            res.macro[series] = store_macro(series, fetch(series, None))
        except Exception as e:
            res.errors.append(f"{series}: {e}")
    return res
