"""Tests for reading a DocILE dataset directory.

Every case builds its own dataset from the synthetic fixture, so the suite
runs in CI with no dataset present.
"""

import shutil
from pathlib import Path

import pytest

from docmatch.docile.dataset import (
    DatasetNotFoundError,
    DocileDataset,
    DocumentNotFoundError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_annotation.json"


@pytest.fixture
def dataset(tmp_path: Path) -> DocileDataset:
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    shutil.copy(FIXTURE, annotations / "syn0001.json")
    return DocileDataset(tmp_path)


def test_loads_the_annotation_for_a_document_id(dataset: DocileDataset) -> None:
    annotation = dataset.annotation("syn0001")

    assert annotation.metadata.document_type == "tax_invoice"
    assert [field.fieldtype for field in annotation.fields[:2]] == [
        "vendor_name",
        "vendor_address",
    ]
    assert [item.line_item_id for item in annotation.line_items] == [1, 2]


def test_names_the_document_it_could_not_find(dataset: DocileDataset) -> None:
    with pytest.raises(DocumentNotFoundError) as raised:
        dataset.annotation("absent0001")

    assert "absent0001" in str(raised.value)


def test_tells_a_missing_dataset_apart_from_a_missing_document(tmp_path: Path) -> None:
    """A dataset that was never downloaded is a different problem to report."""
    absent = tmp_path / "never-downloaded"

    with pytest.raises(DatasetNotFoundError) as raised:
        DocileDataset(absent).annotation("syn0001")

    assert str(absent) in str(raised.value)


@pytest.mark.parametrize("document_id", ["", "../../outside", "syn/0001", "."])
def test_refuses_a_document_id_that_is_not_a_plain_id(
    dataset: DocileDataset, document_id: str
) -> None:
    """Document ids come from the command line, so they never build a path."""
    with pytest.raises(ValueError):
        dataset.annotation(document_id)
