"""Monthly history of the three simulation building blocks, 1871 -> today, NOMINAL returns:

  stocks  US large-cap total return (S&P composite incl. dividends)
  bonds   10-year US Treasury total return
  cash    3-month T-bill
  infl    CPI inflation

Sources (fetched at runtime, cached in data/market/, never committed):
  * Shiller, "U.S. Stock Markets 1871-Present" (ie_data.xls): stocks, bonds, CPI until it ends
  * FRED extension after that: SP500 (+ assumed dividend yield), GS10 priced as a par bond,
    TB3MS, CPIAUCSL
  * cash before 1934 (no T-bill series): constant 2%/yr - flagged in `approx`
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
import numpy as np
import pandas as pd

from pf.market.providers import fred
from pf.settings import get_settings

SHILLER_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
COLS = ("stocks", "bonds", "cash", "infl")
PRE_TBILL_CASH = 0.02
EXT_DIVIDEND_YIELD = 0.014  # S&P 500 dividend yield used to turn price returns into total returns


@dataclass
class History:
    df: pd.DataFrame  # PeriodIndex (M), columns COLS, nominal monthly returns
    approx: pd.DataFrame  # same shape, bool: True where a value is an approximation

    def window(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        return self.df.loc[start:end]  # type: ignore[misc]

    def real(self) -> pd.DataFrame:
        """Inflation-adjusted monthly returns for stocks/bonds/cash."""
        r = (1 + self.df[["stocks", "bonds", "cash"]]).div(1 + self.df["infl"], axis=0) - 1
        return r


# ---------------------------------------------------------------- Shiller
def _shiller_period(x: float) -> pd.Period:
    """Shiller writes months as decimals: 1871.01 = Jan, 1871.1 = Oct (not Jan!)."""
    year = int(x)
    month = round((x - year) * 100)
    return cast(pd.Period, pd.Period(year=year, month=month, freq="M"))


def _num(col: object) -> np.ndarray:
    """Column -> float array, non-numeric cells as NaN (pandas stubs type to_numeric loosely)."""
    return np.asarray(pd.to_numeric(cast(pd.Series, col), errors="coerce"), dtype=float)


def parse_shiller(xls: bytes | Path) -> pd.DataFrame:
    src = io.BytesIO(xls) if isinstance(xls, bytes) else xls
    raw = pd.read_excel(src, sheet_name="Data", header=None, skiprows=8)
    raw = raw[~np.isnan(_num(raw[0]))]
    df = pd.DataFrame(
        {
            "cpi": _num(raw[4]),
            "real_tr": _num(raw[9]),  # real total-return price
            "bond_gross": _num(raw[17]),  # 1 + monthly bond TR
        },
        index=pd.PeriodIndex([_shiller_period(float(x)) for x in raw[0]], freq="M"),
    )
    infl = df["cpi"] / df["cpi"].shift(1) - 1
    real = df["real_tr"] / df["real_tr"].shift(1) - 1
    out = pd.DataFrame(
        {
            "stocks": (1 + real) * (1 + infl) - 1,
            # Shiller's bond row t is the return from month t to t+1 (forward-looking, verified:
            # corr 0.94 vs a par-bond repricing only when aligned that way). Shift it so that
            # EVERY column means "return earned during month t", like the stock column.
            "bonds": df["bond_gross"].shift(1) - 1,
            "infl": infl,
        }
    )
    return out.dropna()


# ---------------------------------------------------------------- FRED extension
def par_bond_return(y_prev: pd.Series, y_now: pd.Series, years: int = 10) -> pd.Series:
    """Monthly total return of a par bond bought at yield y_prev, repriced at y_now one month later
    (semi-annual coupons). Yields as decimals."""
    n = 2 * years
    c = y_prev
    disc = (1 + y_now / 2) ** (-n)
    price = c / y_now * (1 - disc) + disc
    return price - 1 + c / 12


def _monthly(obs: list, how: str = "mean") -> pd.Series:  # type: ignore[type-arg]
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in obs}).sort_index()
    s.index = s.index.to_period("M")  # type: ignore[attr-defined]
    g = s.groupby(level=0)
    return cast(pd.Series, g.last() if how == "last" else g.mean())


def fred_extension(fetch=fred.fetch, start: str = "2015-01") -> pd.DataFrame:  # type: ignore[no-untyped-def]
    sp = _monthly(fetch("SP500", None))  # monthly AVERAGE of daily closes, as Shiller does
    gs10 = _monthly(fetch("GS10", None)) / 100
    cpi = _monthly(fetch("CPIAUCSL", None))
    ext = pd.DataFrame(
        {
            "stocks": sp / sp.shift(1) - 1 + EXT_DIVIDEND_YIELD / 12,
            "bonds": par_bond_return(gs10.shift(1), gs10),
            "infl": cpi / cpi.shift(1) - 1,
        }
    )
    return ext.loc[start:].dropna()


def cash_series(fetch=fred.fetch) -> pd.Series:  # type: ignore[no-untyped-def]
    tb = _monthly(fetch("TB3MS", None)) / 100
    return (tb.shift(1) / 12).dropna()  # earn last month's quoted rate this month


# ---------------------------------------------------------------- assemble
def build_history(shiller: pd.DataFrame, extension: pd.DataFrame, cash: pd.Series) -> History:
    base = shiller.copy()
    ext = extension.loc[extension.index > base.index.max()]
    df = pd.concat([base, ext])
    df["cash"] = cash.reindex(df.index)
    approx = pd.DataFrame(False, index=df.index, columns=list(COLS))
    approx.loc[ext.index, ["stocks", "bonds"]] = True
    missing = df["cash"].isna()
    df.loc[missing, "cash"] = PRE_TBILL_CASH / 12
    approx.loc[missing, "cash"] = True
    return History(cast(pd.DataFrame, df[list(COLS)]), approx)


def history_path() -> Path:
    return get_settings().market_dir / "history_monthly.parquet"


def refresh_history(fetch=fred.fetch, shiller_bytes: bytes | None = None) -> History:  # type: ignore[no-untyped-def]
    if shiller_bytes is None:
        r = httpx.get(SHILLER_URL, timeout=120, follow_redirects=True)
        r.raise_for_status()
        shiller_bytes = r.content
    h = build_history(parse_shiller(shiller_bytes), fred_extension(fetch), cash_series(fetch))
    p = history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    out = h.df.copy()
    for c in COLS:
        out[f"approx_{c}"] = h.approx[c]
    out.index = out.index.astype(str)
    out.to_parquet(p)
    return h


def load_history() -> History:
    p = history_path()
    if not p.exists():
        raise FileNotFoundError("no market history cached yet: run `pf market --history`")
    raw = pd.read_parquet(p)
    raw.index = pd.PeriodIndex(raw.index, freq="M")
    approx = cast(pd.DataFrame, raw[[f"approx_{c}" for c in COLS]]).set_axis(list(COLS), axis=1)
    return History(cast(pd.DataFrame, raw[list(COLS)]), approx)


def annualized(r: pd.Series) -> float:
    return float(np.prod(1 + r.to_numpy()) ** (12 / len(r)) - 1)
