"""Monthly TOTAL-RETURN price series for backtests (dividends reinvested), cached per ticker.

Provider chain: Tiingo (official, needs TIINGO_API_KEY) -> yfinance (unofficial fallback).
Special ticker US-STOCKS-1871: the 1871+ US stock total-return index from pf.market.history.
Only COMPLETE months are kept - the running month is dropped."""

from __future__ import annotations

import datetime as dt
import os
import time
from typing import cast

import httpx
import numpy as np
import pandas as pd

from pf.settings import get_settings

LONG_US = "US-STOCKS-1871"
MAX_AGE_S = 24 * 3600


def _cache_path(ticker: str):  # type: ignore[no-untyped-def]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in ticker.upper())
    return get_settings().market_dir / f"px_{safe}.parquet"


def _complete_months(s: pd.Series, today: dt.date | None = None) -> pd.Series:
    today = today or dt.date.today()
    current = pd.Period(today, freq="M")
    return cast(pd.Series, s[s.index < current])  # type: ignore[operator]


def _from_tiingo(ticker: str, key: str) -> pd.Series:
    r = httpx.get(
        f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
        params={"startDate": "1950-01-01", "resampleFreq": "monthly", "token": key},
        timeout=60,
    )
    r.raise_for_status()
    rows = r.json()
    s = pd.Series({pd.Timestamp(x["date"][:10]): float(x["adjClose"]) for x in rows}).sort_index()
    s.index = pd.PeriodIndex(s.index, freq="M")
    return s.groupby(level=0).last()


def _from_yfinance(ticker: str) -> pd.Series:
    import yfinance as yf

    df = yf.download(ticker, start="1950-01-01", interval="1mo", auto_adjust=True, progress=False)
    if df is None or df.empty:
        raise LookupError(f"no data for {ticker}")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    s = close.dropna().astype(float)
    s.index = pd.PeriodIndex(pd.DatetimeIndex(s.index), freq="M")
    return cast(pd.Series, s.groupby(level=0).last())


def _long_us() -> pd.Series:
    from pf.market.history import load_history

    r = load_history().df["stocks"]
    return cast(pd.Series, (1 + r).cumprod() * 100)


def monthly_prices(ticker: str, refresh: bool = False) -> pd.Series:
    ticker = ticker.strip().upper()
    if ticker == LONG_US:
        return _long_us()
    p = _cache_path(ticker)
    if p.exists() and not refresh and time.time() - p.stat().st_mtime < MAX_AGE_S:
        df = pd.read_parquet(p)
        return pd.Series(df["px"].to_numpy(), index=pd.PeriodIndex(df.index, freq="M"), name=ticker)
    key = os.environ.get("TIINGO_API_KEY")
    try:
        s = _from_tiingo(ticker, key) if key else _from_yfinance(ticker)
    except Exception:
        if key:
            s = _from_yfinance(ticker)  # Tiingo down / unknown symbol: try the fallback
        elif p.exists():  # offline: serve stale cache rather than fail
            df = pd.read_parquet(p)
            return pd.Series(
                df["px"].to_numpy(), index=pd.PeriodIndex(df.index, freq="M"), name=ticker
            )
        else:
            raise
    s = _complete_months(cast(pd.Series, s[np.isfinite(s.to_numpy())]))
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"px": s.to_numpy()}, index=s.index.astype(str)).to_parquet(p)
    s.name = ticker
    return s


# ---------------------------------------------------------------- daily closes
def _daily_cache(ticker: str):  # type: ignore[no-untyped-def]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in ticker.upper())
    return get_settings().market_dir / f"pxd_{safe}.parquet"


def daily_closes(ticker: str, start: dt.date, *, max_age_s: int = 6 * 3600) -> dict[str, float]:
    """Daily closes from `start` to the last trading day: {YYYY-MM-DD: close}, adjusted for
    splits and dividends (total return), cached per ticker. Offline, the last cache is served;
    with no cache at all the error is the caller's to report. Used to compare the portfolio with
    an index and to draw one holding's history, never for booking anything."""
    ticker = ticker.strip().upper()
    p = _daily_cache(ticker)
    cached: dict[str, float] = {}
    if p.exists():
        df = pd.read_parquet(p)
        cached = dict(zip(df["date"], df["px"], strict=True))
        fresh = time.time() - p.stat().st_mtime < max_age_s
        if fresh and cached and min(cached) <= (start + dt.timedelta(days=5)).isoformat():
            return {d: v for d, v in cached.items() if d >= start.isoformat()}
    try:
        import yfinance as yf

        df = yf.download(
            ticker, start=(start - dt.timedelta(days=7)).isoformat(), interval="1d",
            auto_adjust=True, progress=False,
        )  # fmt: skip
        if df is None or df.empty:
            raise LookupError(f"no data for {ticker}")
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna().astype(float)
        days = pd.DatetimeIndex(close.index)
        got = {d.date().isoformat(): float(v) for d, v in zip(days, close.to_numpy(), strict=True)}
    except Exception:
        if cached:
            return {d: v for d, v in cached.items() if d >= start.isoformat()}
        raise
    merged = {**cached, **got}
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": list(merged), "px": list(merged.values())}).sort_values(
        "date"
    ).to_parquet(p, index=False)
    return {d: v for d, v in merged.items() if d >= start.isoformat()}


# ---------------------------------------------------------------- delayed quotes
_QUOTES: dict[str, tuple[float, float, str]] = {}  # symbol -> (fetched at, price, quote time)
QUOTE_AGE_S = 60


def latest_prices(symbols: list[str]) -> dict[str, tuple[float, str]]:
    """The newest price of each US-listed symbol: {symbol: (price, time of the quote)}, from
    one batched request, delayed about 15 minutes, including pre- and after-hours trades.
    Symbols the source does not know are simply absent. For looking, never for booking."""
    now = time.time()
    wanted = sorted({s.strip().upper() for s in symbols if s and " " not in s.strip()})
    stale = [s for s in wanted if s not in _QUOTES or now - _QUOTES[s][0] > QUOTE_AGE_S]
    if stale:
        import yfinance as yf

        df = yf.download(
            stale, period="5d", interval="5m", prepost=True, auto_adjust=False,
            progress=False, group_by="ticker", threads=True,
        )  # fmt: skip
        if df is None or df.empty:
            return {s: (_QUOTES[s][1], _QUOTES[s][2]) for s in wanted if s in _QUOTES}
        for sym in stale:
            try:
                col = cast(pd.Series, df[sym]["Close"] if len(stale) > 1 else df["Close"])
                if isinstance(col, pd.DataFrame):
                    col = cast(pd.Series, col.iloc[:, 0])
                col = col.dropna()
                if col.empty:
                    continue
                stamp = pd.Timestamp(str(col.index[-1])).isoformat()
                _QUOTES[sym] = (now, float(col.iloc[-1]), stamp)
            except (KeyError, IndexError, ValueError):
                continue
    return {s: (_QUOTES[s][1], _QUOTES[s][2]) for s in wanted if s in _QUOTES}
