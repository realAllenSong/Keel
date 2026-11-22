"""Shared helpers for reports: assumptions, account metadata, months, FX into base currency."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Connection, text

from pf.ledger.accounts_config import load_specs
from pf.settings import get_settings

ASSET_CLASSES = (
    "cash",
    "term_deposit",
    "brokerage",
    "retirement",
    "crypto",
    "real_estate",
    "vehicle",
    "other",
)


def account_words(name: str, institution: str | None) -> str:
    """ "Chase 信用卡 0002" for Liabilities:Chase:Card-0002: which of your accounts, in words."""
    parts = name.split(":")
    leaf = parts[-1]
    digits = leaf.rsplit("-", 1)[1] if "-" in leaf and leaf.rsplit("-", 1)[1].isdigit() else ""
    from pf.i18n import tr

    kind = tr("信用卡") if parts[0] == "Liabilities" else tr("账户")
    who = institution or (parts[1] if len(parts) > 1 else name)
    return " ".join(x for x in (who, kind, digits) if x)


# ---------------------------------------------------------------- assumptions
@dataclass(frozen=True)
class Assumptions:
    base_currency: str = "USD"
    expected_return: dict[str, float] = field(default_factory=dict)
    fi_asset_classes: tuple[str, ...] = (
        "cash",
        "term_deposit",
        "brokerage",
        "retirement",
        "crypto",
    )
    withdrawal_rate: float = 0.04
    idle_cash_months: float = 6
    tbill_rate: float = 0.04
    sim_mapping: dict[str, dict[str, float]] = field(default_factory=dict)

    def expected(self, asset_class: str) -> float:
        return float(self.expected_return.get(asset_class, 0.0))


# Defaults that hold with no file at all (a fresh install); config/assumptions.example.yaml shows
# the same values with comments. What you change in Settings is saved to assumptions.local.yaml
# (written by the app) and laid over these.
DEFAULT_ASSUMPTIONS: dict[str, Any] = {
    "base_currency": "USD",
    "expected_return": {
        "cash": 0.04,
        "term_deposit": 0.03,
        "brokerage": 0.08,
        "retirement": 0.08,
        "crypto": 0.0,
        "real_estate": 0.03,
        "vehicle": -0.15,
        "other": 0.0,
    },
    "fi_asset_classes": ["cash", "term_deposit", "brokerage", "retirement", "crypto"],
    "withdrawal_rate": 0.04,
    "idle_cash_months": 6,
    "tbill_rate": 0.04,
    "sim_mapping": {
        "cash": {"cash": 1.0},
        "term_deposit": {"bonds": 1.0},
        "brokerage": {"stocks": 1.0},
        "retirement": {"stocks": 0.8, "bonds": 0.2},
        "crypto": {"stocks": 1.0},
        "other": {"cash": 1.0},
    },
}
SUPPORTED_CURRENCIES = ("USD", "CNY")  # base currency choices; reports convert between these


def assumptions_local_path() -> Path:
    return get_settings().config_dir / "assumptions.local.yaml"


def _merged(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = {**out[k], **v} if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def raw_assumptions(path: Path | None = None) -> dict[str, Any]:
    """Defaults, then the example file (a developer's checkout), then your saved settings."""
    if path is not None:
        return _merged(DEFAULT_ASSUMPTIONS, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    d = get_settings().config_dir
    raw = DEFAULT_ASSUMPTIONS
    for p in (d / "assumptions.example.yaml", d / "assumptions.local.yaml"):
        if p.exists():
            raw = _merged(raw, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    return raw


def load_assumptions(path: Path | None = None) -> Assumptions:
    raw = raw_assumptions(path)
    return Assumptions(
        base_currency=raw.get("base_currency", "USD"),
        expected_return={k: float(v) for k, v in (raw.get("expected_return") or {}).items()},
        fi_asset_classes=tuple(raw.get("fi_asset_classes", Assumptions.fi_asset_classes)),
        withdrawal_rate=float(raw.get("withdrawal_rate", 0.04)),
        idle_cash_months=float(raw.get("idle_cash_months", 6)),
        tbill_rate=_live_tbill(float(raw.get("tbill_rate", 0.04))),
        sim_mapping={
            k: {b: float(w) for b, w in v.items()}
            for k, v in (raw.get("sim_mapping") or {}).items()
        },
    )


def _live_tbill(fallback: float) -> float:
    """Latest 3-month T-bill yield from FRED (DTB3, %) if the daily job has fetched it."""
    try:
        from pf.market.update import latest_macro

        latest = latest_macro("DTB3")
    except Exception:
        return fallback
    return latest[1] / 100 if latest else fallback


# ---------------------------------------------------------------- account metadata
_CLASS_HINTS = (
    ("401k", "retirement"),
    ("ira", "retirement"),
    ("roth", "retirement"),
    ("pension", "retirement"),
    ("brokerage", "brokerage"),
    ("crypto", "crypto"),
    ("coinbase", "crypto"),
    ("house", "real_estate"),
    ("property", "real_estate"),
    ("car", "vehicle"),
    ("termdeposit", "term_deposit"),
    ("checking", "cash"),
    ("savings", "cash"),
    ("alipay", "cash"),
    ("wechat", "cash"),
)


def infer_asset_class(name: str) -> str:
    low = name.lower()
    return next((cls for key, cls in _CLASS_HINTS if key in low), "other")


def sync_account_meta(conn: Connection) -> None:
    """Apply asset_class / institution from config to existing accounts; infer missing classes."""
    for spec in load_specs():
        conn.execute(
            text(
                "UPDATE accounts SET asset_class = COALESCE(:c, asset_class),"
                " institution = COALESCE(:i, institution) WHERE name = :n"
            ),
            {"c": spec.asset_class, "i": spec.institution, "n": spec.name},
        )
    for r in conn.execute(
        text("SELECT id, name FROM accounts WHERE type = 'Assets' AND asset_class IS NULL")
    ).all():
        conn.execute(
            text("UPDATE accounts SET asset_class = :c WHERE id = :i"),
            {"c": infer_asset_class(r.name), "i": r.id},
        )


# ---------------------------------------------------------------- months
def month_end(month: str) -> dt.date:
    y, m = map(int, month.split("-"))
    first_next = dt.date(y + (m == 12), m % 12 + 1, 1)
    return first_next - dt.timedelta(days=1)


def month_start(month: str) -> dt.date:
    y, m = map(int, month.split("-"))
    return dt.date(y, m, 1)


def month_rate_date(month: str, through: str | None = None) -> str:
    """Value the month at its close, or at the cutoff for a partial month."""
    close = month_end(month).isoformat()
    return min(close, through) if through else close


def add_months(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_range(start: str, end: str) -> list[str]:
    out, m = [], start
    while m <= end:
        out.append(m)
        m = add_months(m, 1)
    return out


def latest_month(conn: Connection) -> str | None:
    d = conn.execute(
        text("SELECT MAX(date) FROM transactions WHERE sealed = 1 AND kind != 'opening'")
    ).scalar()
    return d[:7] if d else None


# ---------------------------------------------------------------- FX
@lru_cache(maxsize=1)
def _baseline() -> dict[tuple[str, str], list[tuple[str, Decimal]]]:
    """Monthly rates shipped with Keel (pf/market/fx_baseline.csv): a fresh install, offline or
    before the first daily download, still converts. Downloaded rates always win."""
    from importlib import resources

    out: dict[tuple[str, str], list[tuple[str, Decimal]]] = {}
    text_ = resources.files("pf.market").joinpath("fx_baseline.csv").read_text(encoding="utf-8")
    for line in text_.splitlines():
        if not line or line.startswith(("#", "date,")):
            continue
        date, base, quote, rate = line.split(",")
        out.setdefault((base, quote), []).append((date, Decimal(rate)))
    for rows in out.values():
        rows.sort()
    return out


def _baseline_rate(commodity: str, target: str, date: str) -> Decimal | None:
    table = _baseline()
    for (b, q), invert in (((commodity, target), False), ((target, commodity), True)):
        rows = [r for d, r in table.get((b, q), []) if d <= date]
        if rows and rows[-1] > 0:
            return 1 / rows[-1] if invert else rows[-1]
    return None


class Fx:
    """Converts minor units of any commodity into base-currency cents using the latest rate on
    or before the date. Missing rates are collected in `missing` instead of guessed."""

    def __init__(self, conn: Connection, base: str = "USD") -> None:
        self.conn = conn
        self.base = base
        self.missing: set[str] = set()
        self._missing_by_date: dict[str, set[str]] = {}
        self._prec = {
            r.code: r.precision
            for r in conn.execute(text("SELECT code, precision FROM commodities"))
        }

    @lru_cache(maxsize=4096)  # noqa: B019 - per-request object
    def rate(self, commodity: str, date: str) -> Decimal | None:
        if commodity == self.base:
            return Decimal(1)
        rate = self._pair_rate(commodity, self.base, date)
        if rate is not None or "USD" in (commodity, self.base):
            return rate
        # Securities and other commodities are often quoted in USD only.
        to_usd = self._pair_rate(commodity, "USD", date)
        from_usd = self.rate("USD", date)
        return to_usd * from_usd if to_usd is not None and from_usd is not None else None

    def _pair_rate(self, commodity: str, target: str, date: str) -> Decimal | None:
        row = self.conn.execute(
            text(
                "SELECT rate FROM prices WHERE base = :b AND quote = :q AND date <= :d"
                " ORDER BY date DESC LIMIT 1"
            ),
            {"b": commodity, "q": target, "d": date},
        ).first()
        if row:
            rate = Decimal(row.rate)
            if rate.is_finite() and rate > 0:
                return rate
        inv = self.conn.execute(
            text(
                "SELECT rate FROM prices WHERE base = :q AND quote = :b AND date <= :d"
                " ORDER BY date DESC LIMIT 1"
            ),
            {"b": commodity, "q": target, "d": date},
        ).first()
        rate = Decimal(inv.rate) if inv else None
        if rate is not None and rate.is_finite() and rate > 0:
            return 1 / rate
        return _baseline_rate(commodity, target, date)

    def to_base(self, minor: int, commodity: str, date: str) -> int:
        """Minor units of `commodity` -> base-currency cents (rounded half-even)."""
        if minor == 0 or commodity == self.base:
            return minor
        r = self.rate(commodity, date)
        if r is None:
            self.missing.add(commodity)
            self._missing_by_date.setdefault(date, set()).add(commodity)
            return 0
        value = Decimal(minor).scaleb(-self._prec.get(commodity, 2)) * r
        return int(value.scaleb(2).to_integral_value())

    def missing_on(self, date: str) -> list[str]:
        return sorted(self._missing_by_date.get(date, ()))


def cents(v: int) -> str:
    """Integer cents -> exact decimal string for the API."""
    return str(Decimal(v).scaleb(-2).quantize(Decimal("0.01")))
