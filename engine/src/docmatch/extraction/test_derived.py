"""Tests for the values code adds beside a reading.

How derived values move the score is pinned in `evals/test_run.py`, over the
synthetic corpus; these pin what a derived value is, which no score shows.
"""

from docmatch.extraction.derived import DerivedValue, derive
from docmatch.metrics.fields import Prediction


def test_copies_the_currency_verbatim_and_names_the_field_it_came_from() -> None:
    """Only the scorer resolves `US$` or `usd` to a code."""
    reading = Prediction(
        fields={"amount_due": "US$ 95.00", "amount_total_gross": ["95.00 usd"]}
    )

    assert derive(reading) == (
        DerivedValue("currency_code_amount_due", "US$", "amount_due"),
        DerivedValue("currency_code_amount_due", "usd", "amount_total_gross"),
    )


def test_takes_the_currency_only_from_the_amount_due_and_the_gross_total() -> None:
    reading = Prediction(
        fields={"amount_total_net": "$80.00", "tax_detail_tax": "$15.00"}
    )

    assert derive(reading) == ()


def test_leaves_the_reading_as_the_backend_returned_it() -> None:
    reading = Prediction(fields={"amount_due": "$95.00"})

    derive(reading)

    assert reading.fields == {"amount_due": "$95.00"}


def test_reads_a_currency_name_across_a_line_break() -> None:
    """The scorer collapses whitespace, so a name split over two lines is known."""
    reading = Prediction(fields={"amount_due": "95.00 US\nDOLLARS"})

    assert derive(reading) == (
        DerivedValue("currency_code_amount_due", "US\nDOLLARS", "amount_due"),
    )


def test_adds_the_currency_symbols_a_vendor_returned_beside_an_amount() -> None:
    """Azure's `currency_symbol` on AmountDue and InvoiceTotal (#24)."""
    reading = Prediction(fields={"amount_due": "95.00"})

    assert derive(reading, {"amount_due": ("$",), "amount_total_gross": ("€",)}) == (
        DerivedValue("currency_code_amount_due", "$", "amount_due"),
        DerivedValue("currency_code_amount_due", "€", "amount_total_gross"),
    )


def test_a_vendor_symbol_the_scorer_does_not_know_is_not_derived() -> None:
    assert derive(Prediction(), {"amount_due": ("zł",)}) == ()


def test_a_vendor_symbol_beside_another_amount_is_not_derived() -> None:
    assert derive(Prediction(), {"amount_total_net": ("$",)}) == ()


def test_a_symbol_printed_and_returned_is_derived_once() -> None:
    reading = Prediction(fields={"amount_due": "$95.00"})

    assert derive(reading, {"amount_due": ("$",)}) == (
        DerivedValue("currency_code_amount_due", "$", "amount_due"),
    )
