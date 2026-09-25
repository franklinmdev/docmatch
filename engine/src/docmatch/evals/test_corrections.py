"""Tests for scoring a corrections export against a run, on a hand-built
export and hand-built readings, with the synthetic fixture's labels."""

import json
from pathlib import Path

import pytest

from docmatch.docile.dataset import DocileDataset
from docmatch.evals.corrections import (
    CorrectionsError,
    Export,
    Tally,
    read_corrections,
    reading_digest,
    score_corrections,
)
from docmatch.metrics.fields import Prediction

SYNTHETIC = Path(__file__).parents[3] / "tests" / "evals" / "synthetic"

ELSEWHERE = "0" * 64
"""The digest of a reading no run in these tests read."""

TORQUE_WRENCH = {
    "line_item_quantity": ["1"],
    "line_item_description": ["Torque wrench"],
    "line_item_amount_gross": ["317.50"],
}
"""eval0003's second labeled line."""

DELIVERY = {
    "line_item_quantity": ["1"],
    "line_item_description": ["Delivery"],
    "line_item_amount_gross": ["0.00"],
}
"""A line eval0005's labels do not have."""


@pytest.fixture
def dataset() -> DocileDataset:
    return DocileDataset(SYNTHETIC)


def exported(
    document_id: str, *corrections: dict[str, object], **more: object
) -> Export:
    return Export.model_validate(
        {
            "schema": "loop_test",
            "documents": [
                {
                    "document_id": document_id,
                    "reading": ELSEWHERE,
                    "decision": "approved",
                    "corrections": list(corrections),
                    **more,
                }
            ],
        }
    )


def reading(**lines: object) -> Prediction:
    return Prediction.model_validate(lines)


def test_a_header_correction_is_scored_by_its_field(dataset: DocileDataset) -> None:
    export = exported(
        "eval0003",
        {"kind": "header", "fieldtype": "amount_due", "left": ["400.00"]},
        {"kind": "header", "fieldtype": "document_id", "left": ["INV-9999"]},
    )
    run = {
        "eval0003": reading(fields={"amount_due": "$400.00", "document_id": "INV-1003"})
    }

    scored = score_corrections(export, run, dataset)

    assert scored.header == (
        Tally("amount_due", corrections=1, read_right=1, label_agrees=1),
        Tally("document_id", corrections=1, read_right=0, label_agrees=0),
    )
    assert scored.cells == ()


def test_a_value_left_blank_asserts_the_field_is_not_there(
    dataset: DocileDataset,
) -> None:
    export = exported(
        "eval0003", {"kind": "header", "fieldtype": "vendor_email", "left": []}
    )
    reads_it = {"eval0003": reading(fields={"vendor_email": "a@b.example"})}
    reads_none = {"eval0003": reading(fields={"vendor_email": None})}

    assert score_corrections(export, reads_it, dataset).header == (
        Tally("vendor_email", corrections=1, read_right=0, label_agrees=1),
    )
    assert score_corrections(export, reads_none, dataset).header == (
        Tally("vendor_email", corrections=1, read_right=1, label_agrees=1),
    )


def test_a_cell_correction_finds_its_line_through_the_pairing(
    dataset: DocileDataset,
) -> None:
    """The run reads the corrected line last where the reviewer saw it
    second; the pairing finds it all the same."""
    export = exported(
        "eval0003",
        {
            "kind": "cell",
            "line": 1,
            "fieldtype": "line_item_quantity",
            "left": ["1"],
        },
        lines={"1": TORQUE_WRENCH},
    )
    run = {
        "eval0003": reading(
            line_items=[
                {"line_item_description": "Work gloves", "line_item_quantity": "5"},
                {"line_item_description": "Hex key set", "line_item_quantity": "3"},
                TORQUE_WRENCH,
            ]
        )
    }
    misread = {
        "eval0003": reading(line_items=[{**TORQUE_WRENCH, "line_item_quantity": "7"}])
    }

    assert score_corrections(export, run, dataset).cells == (
        Tally("line_item_quantity", corrections=1, read_right=1, label_agrees=1),
    )
    assert score_corrections(export, misread, dataset).cells == (
        Tally("line_item_quantity", corrections=1, read_right=0, label_agrees=1),
    )


def test_a_line_added_asserts_a_line_with_its_cells(dataset: DocileDataset) -> None:
    export = exported(
        "eval0003", {"kind": "line added", "line": 3, "left": TORQUE_WRENCH}
    )
    reads_it = {"eval0003": reading(line_items=[TORQUE_WRENCH])}
    reads_half = {
        "eval0003": reading(line_items=[{"line_item_description": "Torque wrench"}])
    }

    assert score_corrections(export, reads_it, dataset).lines == (
        Tally("line added", corrections=1, read_right=1, label_agrees=1),
        Tally("line removed", corrections=0, read_right=0, label_agrees=0),
    )
    assert score_corrections(export, reads_half, dataset).lines[0] == Tally(
        "line added", corrections=1, read_right=0, label_agrees=1
    )


def test_a_line_removed_asserts_no_line_pairs_with_it(
    dataset: DocileDataset,
) -> None:
    export = exported("eval0005", {"kind": "line removed", "line": 2, "read": DELIVERY})
    reads_it = {"eval0005": reading(line_items=[DELIVERY])}
    reads_none = {
        "eval0005": reading(line_items=[{"line_item_description": "Junction box"}])
    }

    assert score_corrections(export, reads_it, dataset).lines[1] == Tally(
        "line removed", corrections=1, read_right=0, label_agrees=1
    )
    assert score_corrections(export, reads_none, dataset).lines[1] == Tally(
        "line removed", corrections=1, read_right=1, label_agrees=1
    )


def test_a_document_the_run_did_not_read_is_read_as_nothing(
    dataset: DocileDataset,
) -> None:
    export = exported(
        "eval0003", {"kind": "header", "fieldtype": "amount_due", "left": ["400.00"]}
    )

    scored = score_corrections(export, {}, dataset)

    assert scored.header == (
        Tally("amount_due", corrections=1, read_right=0, label_agrees=1),
    )


def test_corrections_made_on_the_scored_run_are_skipped_and_counted(
    dataset: DocileDataset,
) -> None:
    corrected = reading(fields={"amount_due": "$40.00"})
    export = Export.model_validate(
        {
            "schema": "loop_test",
            "documents": [
                {
                    "document_id": "eval0003",
                    "reading": reading_digest(corrected),
                    "decision": "approved",
                    "corrections": [
                        {"kind": "header", "fieldtype": "amount_due", "left": ["400"]},
                        {"kind": "header", "fieldtype": "vendor_name", "left": []},
                    ],
                },
                {
                    "document_id": "eval0005",
                    "reading": ELSEWHERE,
                    "decision": "rejected",
                    "corrections": [
                        {"kind": "line removed", "line": 2, "read": DELIVERY}
                    ],
                },
            ],
        }
    )

    scored = score_corrections(export, {"eval0003": corrected}, dataset)

    assert scored.skipped == 2
    assert scored.skipped_documents == 1
    assert scored.documents == 1
    assert scored.header == ()
    assert scored.lines[1].corrections == 1


def test_the_digest_is_the_reading_not_how_it_was_written() -> None:
    written = reading(
        fields={"amount_due": "$400.00", "vendor_email": None},
        line_items=[{"line_item_quantity": ["3"]}],
    )
    rewritten = Prediction.model_validate_json(
        json.dumps(
            {
                "line_items": [{"line_item_quantity": "3"}],
                "fields": {"amount_due": ["$400.00"]},
            }
        )
    )

    assert reading_digest(written) == reading_digest(rewritten)
    assert reading_digest(written) != reading_digest(Prediction())


def test_a_file_that_is_not_an_export_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "corrections.json"
    path.write_text('{"documents": [{"document_id": "eval0003"}]}', "utf-8")

    with pytest.raises(CorrectionsError, match="not a corrections export"):
        read_corrections(path)


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(CorrectionsError, match="cannot read"):
        read_corrections(tmp_path / "corrections.json")
