"""Tests for normalizing field values before they are compared.

There is one case per normalization rule, and each case is a pair that differs
only in the formatting its rule removes. Delete the rule and the case goes red.

Every value here is made up to carry a formatting pattern the annotated set
contains. None of them is a DocILE label: the dataset may not be redistributed,
so no label text is committed.
"""

import pytest

from docmatch.metrics.normalization import normalize, reads, reads_number, rule


def agree(fieldtype: str, left: str, right: str) -> bool:
    """Whether two spellings of a value normalize to the same thing."""
    assert left != right, "a normalization case must start from two spellings"
    return normalize(fieldtype, left) == normalize(fieldtype, right)


def test_whitespace_is_collapsed_and_case_is_ignored() -> None:
    assert agree("payment_terms", "NET  30\nDAYS ", "Net 30 days")


def test_a_number_is_read_through_its_currency_and_separators() -> None:
    assert agree("amount_total_gross", "US$ 2,460.00", "2460")


def test_a_decimal_comma_is_read_as_a_decimal_point() -> None:
    assert agree("amount_due", "2.460,00", "$2,460.00")


def test_a_date_is_read_to_one_calendar_day() -> None:
    assert agree("date_issue", "SEP18/26", "09/18/2026")


def test_a_currency_symbol_is_read_as_its_code() -> None:
    assert agree("currency_code_amount_due", "$", "USD")


@pytest.mark.parametrize(
    ("fieldtype", "text", "expected"),
    [
        # A non-breaking space is whitespace only after NFKC; two real labels
        # carry one.
        ("vendor_name", "  Synthetic\xa0Supplies   Ltd ", "synthetic supplies ltd"),
        ("amount_total_gross", "$-1,234.50", "-1234.5"),
        ("amount_total_tax", ".00", "0"),
        ("tax_detail_rate", "7.250%", "7.25"),
        ("date_issue", "September 17th, 2026", "2026-09-17"),
        ("date_due", "27-Oct-2026", "2026-10-27"),
        # 13 cannot be a month, so this pins the month-first reading.
        ("date_issue", "12/13/2026", "2026-12-13"),
        # A two-digit year at or above the pivot is last century.
        ("date_issue", "5 26 94", "1994-05-26"),
        ("currency_code_amount_due", "US$", "usd"),
    ],
)
def test_canonical_forms(fieldtype: str, text: str, expected: str) -> None:
    assert normalize(fieldtype, text) == expected


@pytest.mark.parametrize(
    ("fieldtype", "text"),
    [
        ("tax_detail_rate", "7 1/2%"),
        ("amount_total_gross", "not charged"),
        ("date_issue", "December 2026"),
        ("date_due", "on delivery"),
        ("currency_code_amount_due", "zar"),
    ],
)
def test_a_value_its_rule_cannot_read_falls_back_to_text(
    fieldtype: str, text: str
) -> None:
    """A rule that cannot read a value must not mangle it into a false match."""
    assert normalize(fieldtype, text) == normalize("vendor_name", text)


@pytest.mark.parametrize(
    "text", ["$-1,234.50", "-$1,234.50", "-1234.5", "1,234.50-", "- $ 1.234,50"]
)
def test_a_minus_counts_wherever_the_document_writes_it(text: str) -> None:
    """The annotated set writes the sign before the symbol, after it, and last."""
    assert normalize("amount_due", text) == "-1234.5"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SEP 18, 2026", "2026-09-18"),
        ("Sept 18, 2026", "2026-09-18"),
        ("MARCH 18, 2026", "2026-03-18"),
        ("MAYBE 18, 2026", "maybe 18, 2026"),
        ("JU 18, 2026", "ju 18, 2026"),
    ],
)
def test_a_word_names_a_month_only_when_it_starts_exactly_one(
    text: str, expected: str
) -> None:
    """`JU` could be June or July, and `MAYBE` is not May."""
    assert normalize("date_issue", text) == expected


@pytest.mark.parametrize(
    ("fieldtype", "left", "right"),
    [
        ("line_item_amount_gross", "US$ 2,460.00", "2460"),
        ("line_item_quantity", "2.00", "2"),
        ("line_item_position", "01.", "1"),
        ("line_item_date", "SEP18/26", "2026-09-18"),
        ("line_item_currency", "US$", "usd"),
    ],
)
def test_a_line_item_cell_takes_the_same_rule_as_a_header_field(
    fieldtype: str, left: str, right: str
) -> None:
    assert agree(fieldtype, left, right)


@pytest.mark.parametrize(
    ("fieldtype", "text"),
    [
        ("line_item_code", "0080"),
        ("line_item_order_id", "0080"),
        ("line_item_hts_number", "8471.30.0100"),
    ],
)
def test_an_identifier_keeps_its_zeros_and_dots(fieldtype: str, text: str) -> None:
    """The number rule could read these; reading them would lose the identifier."""
    assert normalize(fieldtype, text) == text


@pytest.mark.parametrize(
    ("fieldtype", "expected"),
    [
        ("amount_due", "number"),
        ("line_item_date", "date"),
        ("currency_code_amount_due", "currency"),
        ("vendor_name", "text"),
    ],
)
def test_a_fieldtype_names_the_rule_its_values_go_through(
    fieldtype: str, expected: str
) -> None:
    assert rule(fieldtype) == expected


@pytest.mark.parametrize(
    ("fieldtype", "text", "expected"),
    [
        ("date_issue", "SEP18/26", True),
        ("date_issue", "on delivery", False),
        ("amount_due", "US$ 2,460.00", True),
        ("amount_due", "not charged", False),
        ("currency_code_amount_due", "US$", True),
        ("currency_code_amount_due", "zar", False),
        ("vendor_name", "Synthetic Supplies Ltd", True),
    ],
)
def test_reading_a_value_is_the_other_half_of_normalizing_it(
    fieldtype: str, text: str, expected: bool
) -> None:
    """A value its rule cannot read is the one the text rule ends up with."""
    assert reads(fieldtype, text) is expected
    assert (normalize(fieldtype, text) != normalize("vendor_name", text)) <= expected


def test_the_number_rule_can_be_asked_about_a_fieldtype_it_does_not_have() -> None:
    """What reading an identifier as a number would cost is why it is text."""
    assert reads_number("8471.30.0100") is True
    assert reads("line_item_hts_number", "8471.30.0100") is True, "the text rule"
