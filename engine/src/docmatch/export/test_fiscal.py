"""Tests for the fiscal rules the 606 applies, each a table with effective dates.

Expected values are written out from the sources quoted in `export.fiscal`.
"""

from datetime import date
from decimal import Decimal

import pytest

from docmatch.export.fiscal import (
    is_consumo,
    is_gastos_menores,
    isr_retention_rate,
)


@pytest.mark.parametrize("retention_type", [1, 2])
def test_rent_and_fees_are_retained_at_10_percent_before_july_2026(
    retention_type: int,
) -> None:
    assert isr_retention_rate(retention_type, date(2026, 6, 30)) == Decimal("0.10")


@pytest.mark.parametrize("retention_type", [1, 2])
def test_rent_and_fees_are_retained_at_15_percent_from_july_2026(
    retention_type: int,
) -> None:
    assert isr_retention_rate(retention_type, date(2026, 7, 1)) == Decimal("0.15")


@pytest.mark.parametrize("retention_type", [3, 4, 5, 6, 7, 8, 9])
def test_a_retention_type_with_no_stated_rate_has_none(retention_type: int) -> None:
    assert isr_retention_rate(retention_type, date(2026, 8, 1)) is None


@pytest.mark.parametrize(
    ("ncf", "expected"),
    [
        ("B0200000001", True),
        ("E320000000019", True),
        ("B0100000001", False),
        ("E310000000019", False),
        ("B1400000001", False),
    ],
)
def test_consumo_is_a_b02_or_an_e32(ncf: str, expected: bool) -> None:
    assert is_consumo(ncf) is expected


@pytest.mark.parametrize(
    ("ncf", "expected"),
    [
        ("B1300000001", True),
        ("E430000000001", True),
        ("B0100000001", False),
        ("E410000000001", False),
        ("B1400000001", False),
    ],
)
def test_gastos_menores_is_a_b13_or_an_e43(ncf: str, expected: bool) -> None:
    assert is_gastos_menores(ncf) is expected
