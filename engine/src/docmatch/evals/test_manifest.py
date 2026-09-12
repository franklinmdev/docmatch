"""Tests for pinning the fixed evaluation subset."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from docmatch.evals.manifest import (
    MANIFEST,
    SEED,
    SIZE,
    SPLIT,
    Manifest,
    ManifestError,
    load,
    select,
    selected,
    write,
)

CORPUS = tuple(f"doc{number:04d}" for number in range(500))


def test_selects_the_size_asked_for() -> None:
    assert len(select(CORPUS, seed=1, size=100)) == 100


def test_selects_the_same_subset_every_time() -> None:
    assert select(CORPUS, seed=1, size=10) == select(CORPUS, seed=1, size=10)


def test_a_different_seed_selects_a_different_subset() -> None:
    assert select(CORPUS, seed=1, size=10) != select(CORPUS, seed=2, size=10)


def test_the_order_of_the_split_file_does_not_move_the_subset() -> None:
    """A split file's order is the dataset's own; the subset cannot depend on it."""
    shuffled = tuple(reversed(CORPUS))

    assert select(shuffled, seed=1, size=10) == select(CORPUS, seed=1, size=10)


def test_a_duplicated_id_is_selected_once() -> None:
    assert select((*CORPUS, *CORPUS), seed=1, size=10) == select(
        CORPUS, seed=1, size=10
    )


def test_a_prefix_of_the_subset_is_itself_a_sample() -> None:
    """Drawing fewer keeps the same documents, so a smaller run is comparable."""
    ten = select(CORPUS, seed=1, size=10)

    assert select(CORPUS, seed=1, size=100)[:10] == ten


def test_refuses_to_select_more_documents_than_the_split_holds() -> None:
    with pytest.raises(ManifestError) as raised:
        select(CORPUS[:9], seed=1, size=10)

    assert "9" in str(raised.value)


def test_a_manifest_records_the_draw_it_came_from() -> None:
    manifest = selected(CORPUS, split="val", seed=1, size=10)

    assert manifest.split == "val"
    assert manifest.seed == 1
    assert manifest.document_ids == select(CORPUS, seed=1, size=10)


def test_a_manifest_reproduces_itself_from_the_split_it_names() -> None:
    manifest = selected(CORPUS, split="val", seed=1, size=10)

    assert manifest.reproduced_from(CORPUS) == manifest.document_ids


def test_a_manifest_whose_size_disagrees_with_its_ids_is_not_a_manifest() -> None:
    with pytest.raises(ValidationError) as raised:
        Manifest(split="val", seed=1, size=2, document_ids=("a",))

    assert "1 ids are listed" in str(raised.value)


def test_reports_a_manifest_whose_size_disagrees_with_its_ids(tmp_path: Path) -> None:
    path = tmp_path / "subset.json"
    path.write_text(
        '{"split": "val", "seed": 1, "size": 2, "document_ids": ["a"]}',
        encoding="utf-8",
    )

    with pytest.raises(ManifestError) as raised:
        load(path)

    assert "1 ids are listed" in str(raised.value)


def test_writes_and_reads_back_the_same_manifest(tmp_path: Path) -> None:
    manifest = selected(CORPUS, split="val", seed=1, size=10)
    path = tmp_path / "subset.json"

    write(manifest, path)

    assert load(path) == manifest


def test_the_written_manifest_is_a_readable_json_object(tmp_path: Path) -> None:
    """A human reads the pinned subset in the diff, so it is not one long line."""
    path = tmp_path / "subset.json"

    write(selected(CORPUS, split="val", seed=1, size=10), path)

    body = path.read_text(encoding="utf-8")
    assert body.endswith("\n")
    assert sorted(json.loads(body)) == ["document_ids", "seed", "size", "split"]


def test_reports_a_manifest_that_is_not_there(tmp_path: Path) -> None:
    absent = tmp_path / "absent.json"

    with pytest.raises(ManifestError) as raised:
        load(absent)

    assert str(absent) in str(raised.value)


def test_reports_a_manifest_that_is_not_a_manifest(tmp_path: Path) -> None:
    path = tmp_path / "subset.json"
    path.write_text('{"seed": "not a number"}', encoding="utf-8")

    with pytest.raises(ManifestError) as raised:
        load(path)

    assert str(path) in str(raised.value)


def test_the_committed_manifest_pins_the_fixed_subset() -> None:
    """The number in the README is over this list and no other."""
    manifest = load(MANIFEST)

    assert manifest.split == SPLIT
    assert manifest.seed == SEED
    assert manifest.size == SIZE
    assert len(set(manifest.document_ids)) == SIZE


def test_a_manifest_that_lists_a_document_twice_is_not_a_manifest() -> None:
    """Ninety-nine documents counted as a hundred is not the fixed subset."""
    with pytest.raises(ValidationError) as raised:
        Manifest(split="val", seed=1, size=2, document_ids=("a", "a"))

    assert "listed more than once" in str(raised.value)
