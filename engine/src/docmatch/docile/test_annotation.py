"""Tests for parsing the DocILE annotation shape.

The fixture is synthetic. Real DocILE documents are never committed.
"""

from pathlib import Path

from docmatch.docile.annotation import Annotation

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_annotation.json"


def synthetic_annotation() -> Annotation:
    return Annotation.model_validate_json(FIXTURE.read_text())


def test_reads_kile_fields_in_document_order() -> None:
    annotation = synthetic_annotation()

    assert [(field.fieldtype, field.text) for field in annotation.fields] == [
        ("vendor_name", "Synthetic Supplies Ltd"),
        ("vendor_address", "12 Example Way\nTestville, EX 00000"),
        ("document_id", "SYN-0001"),
        ("date_issue", "02/01/26"),
        ("amount_total_gross", "236.00"),
    ]


def test_groups_lir_cells_into_line_items_ordered_by_id() -> None:
    """The fixture lists item 2 before item 1; grouping must not depend on order."""
    annotation = synthetic_annotation()

    assert [item.line_item_id for item in annotation.line_items] == [1, 2]
    assert [(cell.fieldtype, cell.text) for cell in annotation.line_items[0].cells] == [
        ("line_item_quantity", "2"),
        ("line_item_description", "Blue widget"),
        ("line_item_amount_gross", "100.00"),
    ]


def test_reads_metadata_and_ignores_keys_it_does_not_model() -> None:
    """Real annotations carry layout keys the engine has no use for yet."""
    metadata = synthetic_annotation().metadata

    assert metadata.page_count == 1
    assert metadata.currency == "eur"
    assert metadata.document_type == "tax_invoice"
