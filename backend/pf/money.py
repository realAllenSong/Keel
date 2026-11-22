"""Exact money arithmetic. Amounts are Decimal in Python and integer minor units in the DB."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from sqlalchemy import Connection, text

DEFAULT_PRECISION = {"currency": 2, "security": 6, "crypto": 8, "other": 6}


class MoneyError(ValueError):
    pass


def parse_decimal(raw: str | int | float | Decimal) -> Decimal:
    """Parse '1,234.50', '(12.00)', '-¥3.5', '$1.00' etc. Floats are rejected on purpose."""
    if isinstance(raw, float):
        raise MoneyError("refusing float input; pass a string")
    if isinstance(raw, Decimal | int):
        return Decimal(raw)
    s = raw.strip().replace(",", "").replace(" ", "").replace(" ", "")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    for sym in ("$", "¥", "￥", "€", "£", "HK$", "US$", "USD", "CNY", "RMB"):
        s = s.replace(sym, "")
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise MoneyError(f"not a number: {raw!r}") from e
    return -d if neg else d


def to_minor(value: Decimal, precision: int, *, exact: bool = True) -> int:
    q = value.scaleb(precision)
    if exact and q != q.to_integral_value():
        raise MoneyError(f"{value} has more than {precision} decimal places")
    return int(q.to_integral_value(rounding=ROUND_HALF_EVEN))


def from_minor(value: int, precision: int) -> Decimal:
    return Decimal(value).scaleb(-precision)


class Commodities:
    """Per-connection cache of commodity precisions; auto-registers unknown securities."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        rows = conn.execute(text("SELECT code, precision FROM commodities")).all()
        self._prec: dict[str, int] = {r.code: r.precision for r in rows}

    def precision(self, code: str, kind: str = "security") -> int:
        if code not in self._prec:
            prec = DEFAULT_PRECISION[kind]
            self.conn.execute(
                text("INSERT INTO commodities(code, kind, precision) VALUES (:c, :k, :p)"),
                {"c": code, "k": kind, "p": prec},
            )
            self._prec[code] = prec
        return self._prec[code]
