"""Tests for what one call costs."""

from decimal import Decimal

from docmatch.extraction.extractor import Price, Usage

FLASH_LITE = Price(
    input_per_million=Decimal("0.25"),
    output_per_million=Decimal("1.50"),
    read="2026-09-14",
)


def test_adds_up_the_tokens_of_a_call() -> None:
    usage = Usage(input_tokens=1309, output_tokens=1131)

    assert usage.total_tokens == 2440


def test_charges_input_and_output_at_their_own_rates() -> None:
    cost = FLASH_LITE.of(Usage(input_tokens=1_000_000, output_tokens=1_000_000))

    assert cost == Decimal("1.75")


def test_keeps_a_document_worth_of_cost_instead_of_rounding_it_away() -> None:
    """A document costs a fraction of a cent, and a hundred of them do not.

    Rounding each document to cents first would report the whole run as free,
    which is the one thing the cost column must never say.
    """
    one = FLASH_LITE.of(Usage(input_tokens=1309, output_tokens=1131))

    assert one > 0
    assert round(one, 2) == Decimal("0.00")
    assert one * 100 > Decimal("0.19")


AZURE_PREBUILT = Price(per_thousand_pages=Decimal("10"), read="2026-09-16")


def test_charges_a_per_page_backend_for_the_pages_it_reported() -> None:
    assert AZURE_PREBUILT.of(Usage(pages=3)) == Decimal("0.03")


def test_a_per_page_price_charges_nothing_for_tokens() -> None:
    assert AZURE_PREBUILT.of(Usage(input_tokens=1_000_000, output_tokens=1)) == 0


def test_adds_up_pages_beside_tokens() -> None:
    both = Usage(input_tokens=1, output_tokens=2, pages=3) + Usage(pages=2)

    assert both == Usage(input_tokens=1, output_tokens=2, pages=5)
