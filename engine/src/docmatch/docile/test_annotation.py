"""Tests for parsing the DocILE annotation shape.

The annotation under test is synthetic. Real DocILE documents are never
committed.
"""

from docmatch.docile.annotation import Annotation


def test_reads_kile_fields_in_document_order(synthetic_annotation: Annotation) -> None:
    assert [(field.fieldtype, field.text) for field in synthetic_annotation.fields] == [
        ("vendor_name", "Synthetic Supplies Ltd"),
        ("vendor_address", "12 Example Way\nTestville, EX 00000"),
        ("document_id", "SYN-0001"),
        ("date_issue", "02/01/26"),
        ("amount_total_gross", "236.00"),
    ]


def test_groups_lir_cells_into_line_items_ordered_by_id(
    synthetic_annotation: Annotation,
) -> None:
    """The file lists item 2 before item 1; grouping must not depend on order."""
    assert [item.line_item_id for item in synthetic_annotation.line_items] == [1, 2]
    assert [
        (cell.fieldtype, cell.text) for cell in synthetic_annotation.line_items[0].cells
    ] == [
        ("line_item_quantity", "2"),
        ("line_item_description", "Blue widget"),
        ("line_item_amount_gross", "100.00"),
    ]


def test_reads_metadata_and_ignores_keys_it_does_not_model(
    synthetic_annotation: Annotation,
) -> None:
    """Real annotations carry layout keys the engine has no use for yet."""
    metadata = synthetic_annotation.metadata

    assert metadata.page_count == 1
    assert metadata.currency == "eur"
    assert metadata.document_type == "tax_invoice"
