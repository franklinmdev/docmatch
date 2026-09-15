"""Tests for pinning the fixed evaluation subset."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from docmatch.evals.manifest import (
    MANIFEST,
    SEED,
    SIZE,
    SOURCE,
    SPLIT,
    Manifest,
    ManifestError,
    Reason,
    Rejected,
    admitted,
    load,
    rank,
    select,
    write,
)

CORPUS = tuple(f"doc{number:04d}" for number in range(500))


def admit_all(document_id: str) -> str:
    return f"digest-of-{document_id}"


def pinned(size: int = 10, **update: object) -> Manifest:
    """A manifest of the first `size` documents seed 1 draws, every one admitted."""
    return admitted(
        CORPUS, split="val", seed=1, source="ucsf", size=size, admit=admit_all
    ).model_copy(update=update)


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


def test_restricting_the_pool_keeps_the_ranking_of_the_ids_left_in_it() -> None:
    """The documents of the old draw that stay in the pool stay in, in order."""
    pool = CORPUS[::2]

    assert rank(pool, seed=1) == tuple(
        document_id for document_id in rank(CORPUS, seed=1) if document_id in pool
    )


def test_admission_pins_the_first_documents_the_ranking_admits() -> None:
    manifest = pinned()

    assert manifest.source == "ucsf"
    assert manifest.document_ids == select(CORPUS, seed=1, size=10)
    assert manifest.rejected == {}
    assert manifest.digests == {
        document_id: f"digest-of-{document_id}" for document_id in manifest.document_ids
    }


def test_admission_walks_past_a_reject_and_records_its_reason() -> None:
    ranked = rank(CORPUS, seed=1)
    reasons: dict[str, Reason] = {
        ranked[1]: "page count differs",
        ranked[4]: "fetch failed",
    }

    def admit(document_id: str) -> str:
        if document_id in reasons:
            raise Rejected(reasons[document_id])
        return admit_all(document_id)

    manifest = admitted(
        CORPUS, split="val", seed=1, source="ucsf", size=10, admit=admit
    )

    assert manifest.rejected == reasons
    assert (
        manifest.document_ids
        == tuple(document_id for document_id in ranked if document_id not in reasons)[
            :10
        ]
    )


def test_admission_stops_asking_once_the_subset_is_full() -> None:
    """Every fetch is a request to the archive, so none is made past the last."""
    asked: list[str] = []

    def admit(document_id: str) -> str:
        asked.append(document_id)
        return admit_all(document_id)

    admitted(CORPUS, split="val", seed=1, source="ucsf", size=10, admit=admit)

    assert asked == list(rank(CORPUS, seed=1)[:10])


def test_admission_takes_every_admitted_document_when_too_few_pass() -> None:
    """The size says how many passed rather than silently meaning something else."""
    ranked = rank(CORPUS[:12], seed=1)

    def admit(document_id: str) -> str:
        if document_id in ranked[:5]:
            raise Rejected("page size differs")
        return admit_all(document_id)

    manifest = admitted(
        CORPUS[:12], split="val", seed=1, source="ucsf", size=10, admit=admit
    )

    assert manifest.size == 7
    assert manifest.document_ids == ranked[5:]
    assert len(manifest.rejected) == 5


def test_admission_that_admits_nothing_pins_nothing() -> None:
    def admit(document_id: str) -> str:
        raise Rejected("fetch failed")

    with pytest.raises(ManifestError) as raised:
        admitted(CORPUS[:3], split="val", seed=1, source="ucsf", size=2, admit=admit)

    assert "3 fetch failed" in str(raised.value)


def test_a_manifest_reproduces_itself_as_ranking_minus_rejects() -> None:
    ranked = rank(CORPUS, seed=1)

    def admit(document_id: str) -> str:
        if document_id == ranked[0]:
            raise Rejected("page size differs")
        return admit_all(document_id)

    manifest = admitted(
        CORPUS, split="val", seed=1, source="ucsf", size=10, admit=admit
    )

    assert manifest.reproduced_from(CORPUS) == manifest.document_ids


def test_a_reject_that_was_forgotten_is_drift() -> None:
    ranked = rank(CORPUS, seed=1)

    def admit(document_id: str) -> str:
        if document_id == ranked[0]:
            raise Rejected("page size differs")
        return admit_all(document_id)

    forgot = admitted(
        CORPUS, split="val", seed=1, source="ucsf", size=10, admit=admit
    ).model_copy(update={"rejected": {}})

    assert forgot.reproduced_from(CORPUS) != forgot.document_ids


def test_a_prefix_keeps_the_rejects_and_the_digests_of_its_documents() -> None:
    manifest = pinned(rejected={"elsewhere": "fetch failed"})

    prefix = manifest.first(3)

    assert prefix.document_ids == manifest.document_ids[:3]
    assert prefix.rejected == manifest.rejected
    assert prefix.digests == {
        document_id: manifest.digests[document_id]
        for document_id in manifest.document_ids[:3]
    }


def test_a_manifest_whose_size_disagrees_with_its_ids_is_not_a_manifest() -> None:
    with pytest.raises(ValidationError) as raised:
        Manifest(split="val", seed=1, source="ucsf", size=2, document_ids=("a",))

    assert "1 ids are listed" in str(raised.value)


def test_a_manifest_that_pins_a_document_it_rejected_is_not_a_manifest() -> None:
    with pytest.raises(ValidationError) as raised:
        Manifest(
            split="val",
            seed=1,
            source="ucsf",
            size=1,
            document_ids=("a",),
            rejected={"a": "fetch failed"},
        )

    assert "both pinned and rejected" in str(raised.value)


def test_a_reject_needs_one_of_the_known_reasons() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(
            {
                "split": "val",
                "seed": 1,
                "source": "ucsf",
                "size": 1,
                "document_ids": ["a"],
                "rejected": {"b": "looked odd"},
            }
        )


def test_a_manifest_that_digests_some_of_its_documents_is_not_a_manifest() -> None:
    """A document with no digest would be read unverified, so it is all or none."""
    with pytest.raises(ValidationError) as raised:
        Manifest(
            split="val",
            seed=1,
            source="ucsf",
            size=2,
            document_ids=("a", "b"),
            digests={"a": "0" * 64},
        )

    assert "1 of the 2 documents" in str(raised.value)


def test_reports_a_manifest_whose_size_disagrees_with_its_ids(tmp_path: Path) -> None:
    path = tmp_path / "subset.json"
    path.write_text(
        '{"split": "val", "seed": 1, "source": "ucsf", "size": 2, '
        '"document_ids": ["a"]}',
        encoding="utf-8",
    )

    with pytest.raises(ManifestError) as raised:
        load(path)

    assert "1 ids are listed" in str(raised.value)


def test_writes_and_reads_back_the_same_manifest(tmp_path: Path) -> None:
    manifest = pinned(rejected={"elsewhere": "page count differs"})
    path = tmp_path / "subset.json"

    write(manifest, path)

    assert load(path) == manifest


def test_the_written_manifest_is_a_readable_json_object(tmp_path: Path) -> None:
    """A human reads the pinned subset in the diff, so it is not one long line."""
    path = tmp_path / "subset.json"

    write(pinned(), path)

    body = path.read_text(encoding="utf-8")
    assert body.endswith("\n")
    assert sorted(json.loads(body)) == [
        "digests",
        "document_ids",
        "rejected",
        "seed",
        "size",
        "source",
        "split",
    ]


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
    assert manifest.source == SOURCE
    assert 0 < manifest.size <= SIZE
    assert set(manifest.digests) == set(manifest.document_ids)


def test_a_manifest_that_lists_a_document_twice_is_not_a_manifest() -> None:
    """Ninety-nine documents counted as a hundred is not the fixed subset."""
    with pytest.raises(ValidationError) as raised:
        Manifest(split="val", seed=1, source="ucsf", size=2, document_ids=("a", "a"))

    assert "listed more than once" in str(raised.value)
