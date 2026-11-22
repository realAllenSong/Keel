from decimal import Decimal as D

import pytest

from pf.money import MoneyError, from_minor, parse_decimal, to_minor


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234.50", "1234.50"),
        ("(12.00)", "-12.00"),
        ("-$3.50", "-3.50"),
        ("¥-8.8", "-8.8"),
        ("+42", "42"),
        ("  7.10 ", "7.10"),
    ],
)
def test_parse_decimal(raw: str, expected: str) -> None:
    assert parse_decimal(raw) == D(expected)


def test_parse_rejects_float_and_garbage() -> None:
    with pytest.raises(MoneyError):
        parse_decimal(0.1)  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        parse_decimal("abc")


def test_minor_roundtrip_is_exact() -> None:
    assert to_minor(D("0.1") + D("0.2"), 2) == 30
    assert from_minor(to_minor(D("123.45"), 2), 2) == D("123.45")
    assert to_minor(D("0.12345678"), 8) == 12345678
    with pytest.raises(MoneyError):
        to_minor(D("1.005"), 2)
