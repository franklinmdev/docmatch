"""Tests for reading a DocILE dataset directory.

Every case builds its own dataset from the synthetic fixture, so the suite
runs in CI with no dataset present.
"""

from pathlib import Path

import pytest

from docmatch.docile.dataset import (
    DatasetNotFoundError,
    DocileDataset,
    DocumentNotFoundError,
    SplitNotFoundError,
)


@pytest.fixture
def dataset(data_dir: Path) -> DocileDataset:
    return DocileDataset(data_dir)


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


def test_reads_the_document_ids_of_a_split(dataset: DocileDataset) -> None:
    assert dataset.document_ids("val") == ("syn0002", "syn0001")


def test_names_the_split_file_it_could_not_find(dataset: DocileDataset) -> None:
    with pytest.raises(SplitNotFoundError) as raised:
        dataset.document_ids("test")

    assert "test.json" in str(raised.value)


def test_tells_a_missing_dataset_apart_from_a_missing_split(tmp_path: Path) -> None:
    absent = tmp_path / "never-downloaded"

    with pytest.raises(DatasetNotFoundError) as raised:
        DocileDataset(absent).document_ids("val")

    assert str(absent) in str(raised.value)


@pytest.mark.parametrize("split", ["", "../val", "val/all", "."])
def test_refuses_a_split_name_that_is_not_a_plain_name(
    dataset: DocileDataset, split: str
) -> None:
    """Split names come from the command line, so they never build a path."""
    with pytest.raises(ValueError):
        dataset.document_ids(split)
