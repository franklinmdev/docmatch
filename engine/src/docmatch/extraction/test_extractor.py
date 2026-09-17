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


LUNA = Price(
    input_per_million=Decimal("0.20"),
    cached_input_per_million=Decimal("0.02"),
    cache_write_per_million=Decimal("0.25"),
    output_per_million=Decimal("1.20"),
    read="2026-09-16",
)


def test_charges_cached_input_at_the_cached_rate_and_the_rest_at_the_input_rate() -> (
    None
):
    """Cached input tokens are part of the input count, billed at their own rate."""
    usage = Usage(
        input_tokens=1_000_000, cached_input_tokens=400_000, output_tokens=1_000_000
    )

    assert LUNA.of(usage) == Decimal("0.12") + Decimal("0.008") + Decimal("1.20")


def test_a_usage_with_no_cached_count_charges_all_input_at_the_input_rate() -> None:
    assert LUNA.of(Usage(input_tokens=1_000_000)) == Decimal("0.20")


def test_adds_up_cached_input_tokens_beside_the_rest() -> None:
    both = Usage(input_tokens=10, cached_input_tokens=4) + Usage(
        input_tokens=5, cached_input_tokens=1
    )

    assert both == Usage(input_tokens=15, cached_input_tokens=5)


def test_charges_cache_writes_at_their_own_rate_out_of_the_input_count() -> None:
    """GPT-5.6 bills a token written to the cache at 1.25 times the input rate."""
    usage = Usage(
        input_tokens=1_000_000, cached_input_tokens=400_000, cache_write_tokens=500_000
    )

    assert LUNA.of(usage) == Decimal("0.02") + Decimal("0.008") + Decimal("0.125")


def test_adds_up_cache_writes_beside_the_rest() -> None:
    both = Usage(cache_write_tokens=3) + Usage(cache_write_tokens=4)

    assert both == Usage(cache_write_tokens=7)
