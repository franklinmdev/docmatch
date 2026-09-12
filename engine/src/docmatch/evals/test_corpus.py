"""Tests for the survey that recomputes the counts the docstrings assert.

Every case is a corpus built here, small enough that its counts can be read off
the fixture. The real numbers live in the docstrings the survey reproduces;
what these pin is that each probe counts the thing its name says.
"""

from collections.abc import Sequence

from docmatch.docile.annotation import (
    Annotation,
    DocumentMetadata,
    FieldExtraction,
    LineItemCell,
)
from docmatch.evals.corpus import Survey, survey

BOX = (0.0, 0.0, 1.0, 1.0)
"""Where a label sits, which no count here depends on."""

Labels = tuple[tuple[str, str], ...]
"""A fieldtype and its text, the way a fixture writes a label."""


def header(*labels: tuple[str, str]) -> list[FieldExtraction]:
    return [
        FieldExtraction(fieldtype=fieldtype, text=text, page=0, bbox=BOX)
        for fieldtype, text in labels
    ]


def table(*rows: Labels) -> list[LineItemCell]:
    return [
        LineItemCell(
            fieldtype=fieldtype, text=text, page=0, bbox=BOX, line_item_id=line_item_id
        )
        for line_item_id, row in enumerate(rows, start=1)
        for fieldtype, text in row
    ]


def document(
    fields: Sequence[FieldExtraction] = (), cells: Sequence[LineItemCell] = ()
) -> Annotation:
    return Annotation(
        fields=list(fields), cells=list(cells), metadata=DocumentMetadata(page_count=1)
    )


def surveyed(*annotations: Annotation) -> Survey:
    return survey(annotations, split="val")


def coverage(surveyed: Survey, fieldtype: str) -> tuple[int, int, int]:
    """One fieldtype's labels, the ones its rule reads, and the ones a number is."""
    found = [each for each in surveyed.coverage if each.fieldtype == fieldtype]
    assert len(found) == 1, f"{fieldtype} is counted once or not at all"
    return found[0].labels, found[0].read, found[0].as_a_number


def test_counts_the_documents_the_labels_and_the_rows() -> None:
    report = surveyed(
        document(
            header(("vendor_name", "Synthetic Supplies Ltd")),
            table((("line_item_quantity", "2"),), (("line_item_quantity", "1"),)),
        ),
        document(header(("vendor_name", "Other Supplies Ltd"))),
    )

    assert (report.corpus.documents, report.corpus.fields) == (2, 2)
    assert (report.corpus.cells, report.corpus.rows) == (2, 2)


def test_counts_the_documents_that_carry_no_table_and_the_largest_one() -> None:
    """355 of the annotated documents have no line items, and the largest has 110."""
    report = surveyed(
        document(header(("vendor_name", "Synthetic Supplies Ltd"))),
        document(cells=table((("line_item_quantity", "2"),))),
        document(
            cells=table(
                (("line_item_quantity", "2"),),
                (("line_item_quantity", "1"),),
                (("line_item_quantity", "4"),),
            )
        ),
    )

    assert report.corpus.without_a_table == 1
    assert report.corpus.largest_table == 3


def test_coverage_is_the_share_of_a_fieldtype_its_own_rule_reads() -> None:
    report = surveyed(
        document(
            header(
                ("date_issue", "02/01/26"),
                ("date_issue", "on delivery"),
                ("vendor_name", "Synthetic Supplies Ltd"),
            )
        )
    )

    assert coverage(report, "date_issue")[:2] == (2, 1)
    assert coverage(report, "vendor_name")[:2] == (1, 1), "the text rule reads anything"


def test_coverage_by_rule_keeps_the_header_and_the_cells_apart() -> None:
    """The header share and the per-cell share are two separate claims."""
    report = surveyed(
        document(
            header(("date_issue", "02/01/26"), ("date_due", "on delivery")),
            table((("line_item_date", "03/01/26"),)),
        )
    )

    assert [(each.rule, each.read, each.labels) for each in report.header_rules] == [
        ("date", 1, 2)
    ]
    assert [(each.rule, each.read, each.labels) for each in report.cell_rules] == [
        ("date", 1, 1)
    ]


def test_counts_what_the_number_rule_would_read_of_a_text_fieldtype() -> None:
    """`line_item_code` is text because that share is high, not because it is low."""
    report = surveyed(
        document(
            cells=table(
                (("line_item_code", "0080"),),
                (("line_item_code", "A1-B2"),),
            )
        )
    )

    assert coverage(report, "line_item_code") == (2, 2, 1)


def test_counts_the_values_a_lone_dot_makes_ambiguous_by_fieldtype() -> None:
    """A lone dot with three digits after it is the one shape the readings differ on."""
    report = surveyed(
        document(
            header(
                ("amount_total_gross", "209.451"),
                ("amount_due", "$2,460.00"),
                ("tax_detail_rate", "7.250%"),
                ("tax_detail_rate", "7.25%"),
                ("document_id", "SYN.0001"),
            )
        )
    )

    assert report.lone_dot == (("amount_total_gross", 1), ("tax_detail_rate", 1)), (
        "an identifier's dot is text under either reading, so it is not counted"
    )


def test_counts_which_ordering_an_ambiguous_numeric_date_proves() -> None:
    report = surveyed(
        document(
            header(
                ("date_issue", "12/13/2026"),
                ("date_issue", "13/12/2026"),
                ("date_issue", "02/01/26"),
                ("date_issue", "September 17, 2026"),
                ("date_issue", "2026-12-13"),
            )
        )
    )

    assert (report.dates.month_first, report.dates.day_first) == (1, 1)


def test_counts_the_date_labels_the_month_rules_strictness_turns_on() -> None:
    """A German `JUNI` is not read; a three-letter rule would read `MAYBE` as May."""
    report = surveyed(
        document(
            header(
                ("date_issue", "3. JUNI 2026"),
                ("date_issue", "MAYBE 18, 2026"),
                ("date_issue", "SEP 18, 2026"),
                ("date_issue", "on delivery"),
            )
        )
    )

    assert report.dates.three_letter_months == 2


def test_counts_the_header_fieldtypes_labeled_more_than_once() -> None:
    """A value localized twice is one value; two tax rates are two."""
    report = surveyed(
        document(
            header(
                ("vendor_name", "Synthetic Supplies Ltd"),
                ("vendor_name", "SYNTHETIC SUPPLIES LTD"),
                ("tax_detail_rate", "7.25%"),
                ("tax_detail_rate", "5%"),
                ("document_id", "SYN-0001"),
            )
        )
    )

    assert (report.repeats.fieldtypes, report.repeats.one_value) == (2, 1)


def test_counts_a_label_whose_own_text_repeats_a_line() -> None:
    """One box around two letterhead lines is the repetition no rule reaches."""
    report = surveyed(
        document(
            header(
                ("vendor_address", "Synthetic Supplies Ltd\nSynthetic Supplies Ltd"),
                ("vendor_address", "12 Example Way\nTestville, EX 00000"),
            )
        )
    )

    assert report.repeats.within_a_label == 1


def test_counts_the_rows_that_repeat_a_fieldtype_before_and_after_normalizing() -> None:
    """A service period is two `line_item_date` cells; one day written twice is one."""
    report = surveyed(
        document(
            cells=table(
                (("line_item_date", "01/02/2026"), ("line_item_date", "1/2/26")),
                (("line_item_date", "01/02/2026"), ("line_item_date", "01/03/2026")),
                (("line_item_quantity", "2"),),
            )
        )
    )

    assert (report.repeats.rows, report.repeats.rows_after_normalization) == (2, 1)


def test_carries_the_split_the_counts_are_over() -> None:
    assert surveyed(document()).split == "val"
