"""Tests for admitting and downloading public copies, on fixture PDFs only."""

import hashlib
import json
from pathlib import Path

import pytest

from docmatch.docile.dataset import DocileDataset
from docmatch.evals import public
from docmatch.evals.manifest import Manifest, Rejected
from docmatch.evals.public import DigestError, FetchError, PublicCopyError
from docmatch.extraction.conftest import write_pdf

LETTER_AT_200_DPI = [1700, 2200]
"""612 by 792 points, 72 to the inch, at 200 dots per inch."""


def annotate(
    root: Path,
    document_id: str,
    *,
    original_filename: str,
    page_sizes: list[list[int]],
    source: str = "ucsf",
) -> None:
    """A header-only annotation whose metadata is what admission reads."""
    annotations = root / "annotations"
    annotations.mkdir(parents=True, exist_ok=True)
    body = {
        "field_extractions": [],
        "line_item_extractions": [],
        "metadata": {
            "original_filename": original_filename,
            "page_count": len(page_sizes),
            "page_sizes_at_200dpi": page_sizes,
            "source": source,
        },
    }
    (annotations / f"{document_id}.json").write_text(json.dumps(body), "utf-8")


def pdf_bytes(tmp_path: Path, **shape: int) -> bytes:
    return write_pdf(tmp_path / "copy.pdf", **shape).read_bytes()


def admit(tmp_path: Path, copy: bytes, page_sizes: list[list[int]]) -> str:
    """Admit one document whose public copy is `copy`, as `subset --write` would."""
    root = tmp_path / "dataset"
    annotate(root, "doc1", original_filename="abcd1234", page_sizes=page_sizes)
    asked: list[str] = []

    def fetch(ucsf_id: str) -> bytes:
        asked.append(ucsf_id)
        return copy

    digest = public.admitter(DocileDataset(root), fetch)("doc1")
    assert asked == ["abcd1234"]
    return digest


def test_the_archive_nests_a_copy_under_the_first_four_characters_of_its_id() -> None:
    assert public.url("abcd1234") == (
        "https://download.industrydocuments.ucsf.edu/a/b/c/d/abcd1234/abcd1234.pdf"
    )


def test_admits_a_copy_with_docile_pages_and_pins_its_digest(tmp_path: Path) -> None:
    copy = pdf_bytes(tmp_path, pages=2)

    digest = admit(tmp_path, copy, [LETTER_AT_200_DPI, LETTER_AT_200_DPI])

    assert digest == hashlib.sha256(copy).hexdigest()


@pytest.mark.parametrize("size", [[1701, 2200], [1700, 2199], [1699, 2201]])
def test_admits_a_page_within_a_pixel_at_200_dpi(
    tmp_path: Path, size: list[int]
) -> None:
    """DocILE's sizes are whole pixels, so a rounding apart is the same page."""
    admit(tmp_path, pdf_bytes(tmp_path), [size])


@pytest.mark.parametrize("size", [[1530, 1980], [1875, 2426], [1529, 1981]])
def test_admits_the_same_page_at_another_scale(tmp_path: Path, size: list[int]) -> None:
    """Labels are page-relative boxes, so a uniformly rescaled page is the same page.

    The archive's PDF can declare a page box at another scale than the one
    DocILE's sizes imply, with the aspect ratio intact: 47 of the 53 documents
    the 200 dpi rule alone rejected on the val split were that and nothing else.
    """
    admit(tmp_path, pdf_bytes(tmp_path), [size])


@pytest.mark.parametrize("size", [[1705, 2200], [1700, 2210], [1530, 2200]])
def test_rejects_a_page_of_another_shape(tmp_path: Path, size: list[int]) -> None:
    """No single scale puts both sides within a pixel of DocILE's."""
    with pytest.raises(Rejected) as raised:
        admit(tmp_path, pdf_bytes(tmp_path), [size])

    assert raised.value.reason == "page size differs"


def test_measures_a_rotated_page_the_way_it_is_rendered(tmp_path: Path) -> None:
    """A backend reads the page upright, and that is the page the labels are on."""
    admit(tmp_path, pdf_bytes(tmp_path, rotate=90), [[2200, 1700]])


def test_rejects_a_copy_with_a_page_docile_does_not_have(tmp_path: Path) -> None:
    """A trailing page the labels were never drawn on is not the same document."""
    with pytest.raises(Rejected) as raised:
        admit(tmp_path, pdf_bytes(tmp_path, pages=2), [LETTER_AT_200_DPI])

    assert raised.value.reason == "page count differs"


def test_rejects_a_copy_the_archive_would_not_serve(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    annotate(root, "doc1", original_filename="abcd1234", page_sizes=[[1700, 2200]])

    def fetch(ucsf_id: str) -> bytes:
        raise FetchError(f"cannot fetch {ucsf_id}: 404")

    with pytest.raises(Rejected) as raised:
        public.admitter(DocileDataset(root), fetch)("doc1")

    assert raised.value.reason == "fetch failed"


def test_rejects_a_response_that_is_not_a_pdf(tmp_path: Path) -> None:
    with pytest.raises(Rejected) as raised:
        admit(tmp_path, b"<html>not found</html>", [LETTER_AT_200_DPI])

    assert raised.value.reason == "fetch failed"


@pytest.fixture
def pinned(tmp_path: Path) -> tuple[DocileDataset, Manifest, dict[str, bytes]]:
    """Two pinned documents, their copies in the archive, and their digests."""
    root = tmp_path / "dataset"
    archive = {
        "aaaa0001": pdf_bytes(tmp_path, pages=1),
        "bbbb0002": pdf_bytes(tmp_path, pages=2),
    }
    annotate(root, "doc1", original_filename="aaaa0001", page_sizes=[[1700, 2200]])
    annotate(root, "doc2", original_filename="bbbb0002", page_sizes=[[1700, 2200]] * 2)
    manifest = Manifest(
        split="val",
        seed=1,
        source="ucsf",
        size=2,
        document_ids=("doc1", "doc2"),
        digests={
            "doc1": hashlib.sha256(archive["aaaa0001"]).hexdigest(),
            "doc2": hashlib.sha256(archive["bbbb0002"]).hexdigest(),
        },
    )
    return DocileDataset(root), manifest, archive


def test_downloads_every_pinned_copy_by_document_id(
    tmp_path: Path, pinned: tuple[DocileDataset, Manifest, dict[str, bytes]]
) -> None:
    dataset, manifest, archive = pinned
    copies = tmp_path / "copies"

    downloaded = public.download(dataset, manifest, copies, archive.__getitem__)

    assert downloaded == public.Downloaded(fetched=("doc1", "doc2"), kept=())
    assert public.path(copies, "doc2").read_bytes() == archive["bbbb0002"]


def test_keeps_a_copy_already_there_without_asking_the_archive(
    tmp_path: Path, pinned: tuple[DocileDataset, Manifest, dict[str, bytes]]
) -> None:
    dataset, manifest, archive = pinned
    copies = tmp_path / "copies"
    public.download(dataset, manifest, copies, archive.__getitem__)

    def offline(ucsf_id: str) -> bytes:
        raise AssertionError(f"asked the archive for {ucsf_id}")

    downloaded = public.download(dataset, manifest, copies, offline)

    assert downloaded == public.Downloaded(fetched=(), kept=("doc1", "doc2"))


def test_replaces_a_local_copy_that_is_not_the_pinned_one(
    tmp_path: Path, pinned: tuple[DocileDataset, Manifest, dict[str, bytes]]
) -> None:
    dataset, manifest, archive = pinned
    copies = tmp_path / "copies"
    copies.mkdir()
    public.path(copies, "doc1").write_bytes(b"truncated")

    downloaded = public.download(dataset, manifest, copies, archive.__getitem__)

    assert downloaded.fetched == ("doc1", "doc2")
    assert public.path(copies, "doc1").read_bytes() == archive["aaaa0001"]


def test_fails_loudly_when_the_archive_serves_a_different_file(
    tmp_path: Path, pinned: tuple[DocileDataset, Manifest, dict[str, bytes]]
) -> None:
    dataset, manifest, archive = pinned
    copies = tmp_path / "copies"
    changed = {**archive, "bbbb0002": archive["bbbb0002"] + b"\n% rescanned"}

    with pytest.raises(DigestError) as raised:
        public.download(dataset, manifest, copies, changed.__getitem__)

    assert "doc2" in str(raised.value)
    assert manifest.digests["doc2"] in str(raised.value)
    assert not public.path(copies, "doc2").exists()


def test_refuses_a_manifest_that_pins_no_digests(
    tmp_path: Path, pinned: tuple[DocileDataset, Manifest, dict[str, bytes]]
) -> None:
    dataset, manifest, archive = pinned

    with pytest.raises(PublicCopyError) as raised:
        public.download(
            dataset,
            manifest.model_copy(update={"digests": {}}),
            tmp_path / "copies",
            archive.__getitem__,
        )

    assert "no digests" in str(raised.value)


def test_a_fetch_that_fails_during_download_is_an_error_not_a_reject(
    tmp_path: Path, pinned: tuple[DocileDataset, Manifest, dict[str, bytes]]
) -> None:
    """A pinned document cannot be left out after the fact."""
    dataset, manifest, _ = pinned

    def fetch(ucsf_id: str) -> bytes:
        raise FetchError(f"cannot fetch {ucsf_id}")

    with pytest.raises(FetchError):
        public.download(dataset, manifest, tmp_path / "copies", fetch)
