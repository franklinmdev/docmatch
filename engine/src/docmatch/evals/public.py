"""Public copies: fetching them, admitting them, and keeping them pinned.

A hosted backend reads a document's public copy, the PDF its archive publishes,
rather than the copy DocILE redistributes, whose terms bar third-party access
(#16). The labels still come from DocILE, so a copy is only usable when its
pages are DocILE's pages. This module holds the three things that follow: the
route a copy is fetched by, the admission check, and the download that puts
the pinned copies under the ignored data directory and verifies each one.

The route is the UCSF Industry Documents Library's download host, found in the
library's own web app and not in its documentation (#17, read 2026-09-14). It
may change without notice. A changed route is a failed fetch: while the
manifest is being written that is a reject reason, and afterwards it is an
error, because a pinned document cannot be left out after the fact.

HTTP is the standard library's `urllib.request` (Python 3.12 documentation,
read 2026-09-15): redirects are followed, an error status raises `HTTPError`,
and a transport failure raises `URLError`, both `OSError`s. A connection that
drops partway through the body raises `http.client.IncompleteRead` from
`read`, which is an `HTTPException` and not an `OSError`.
"""

import hashlib
import http.client
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from docmatch.docile.annotation import DocumentMetadata
from docmatch.docile.dataset import DocileDataset
from docmatch.evals.manifest import Manifest, Rejected

ROUTE = "https://download.industrydocuments.ucsf.edu/{c1}/{c2}/{c3}/{c4}/{id}/{id}.pdf"
"""Where the archive serves a document's PDF, nested by the id's first four
characters (#17)."""

TIMEOUT = 60.0
"""Seconds to wait on the archive before a fetch counts as failed."""

TOLERANCE = 1
"""Pixels each side of a page may differ by from DocILE's and still be the same page.

DocILE records its sizes in whole pixels at 200 dpi and a PDF measures points,
so a rounding apart is expected: #17 saw exactly that on most pages.
"""

Fetch = Callable[[str], bytes]
"""The public copy of a document, by its id in the archive."""


class PublicCopyError(Exception):
    """A pinned public copy cannot be had, or is not the one that was pinned."""


class FetchError(PublicCopyError):
    """The archive did not serve the copy."""


class DigestError(PublicCopyError):
    """The archive served a copy that is not the one the manifest pins."""


def url(ucsf_id: str) -> str:
    """Where the archive serves this document's PDF."""
    return ROUTE.format(
        c1=ucsf_id[0], c2=ucsf_id[1], c3=ucsf_id[2], c4=ucsf_id[3], id=ucsf_id
    )


def fetch(ucsf_id: str) -> bytes:
    """The public copy, read from the archive over the network."""
    request = urllib.request.Request(url(ucsf_id), headers={"User-Agent": "docmatch"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body: bytes = response.read()
    except (OSError, http.client.HTTPException) as error:
        raise FetchError(f"cannot fetch {url(ucsf_id)}: {error}") from error
    return body


def digest(copy: bytes) -> str:
    """The sha256 of a copy, hex, as the manifest pins it."""
    return hashlib.sha256(copy).hexdigest()


def admitter(dataset: DocileDataset, fetch: Fetch = fetch) -> Callable[[str], str]:
    """Admission for one document at a time, as `manifest.admitted` asks for it."""

    def admit(document_id: str) -> str:
        metadata = dataset.annotation(document_id).metadata
        try:
            copy = fetch(metadata.original_filename)
        except FetchError:
            raise Rejected("fetch failed") from None
        _check(copy, metadata)
        return digest(copy)

    return admit


def _check(copy: bytes, metadata: DocumentMetadata) -> None:
    """Raise `Rejected` unless the copy has DocILE's pages.

    Sizes are measured the way a page is rendered, with its `/Rotate` applied,
    since that is the page a backend reads. A response that is not a readable
    PDF was not a copy at all, so it counts as a failed fetch.
    """
    try:
        document = pdfium.PdfDocument(copy)
    except Exception:  # pdfium raises its own types for a bad file
        raise Rejected("fetch failed") from None
    try:
        if len(document) != metadata.page_count:
            raise Rejected("page count differs")
        for number, (width, height) in enumerate(metadata.page_sizes_at_200dpi):
            if not _same_shape(document[number].get_size(), width, height):
                raise Rejected("page size differs")
    finally:
        document.close()


def _same_shape(points: tuple[float, float], width: int, height: int) -> bool:
    """Whether one scale puts both sides of the page within a pixel of DocILE's.

    Not a fixed 200 dpi. The archive's PDF can declare its page box at another
    scale than DocILE's sizes imply, with the aspect ratio intact, and labels
    are boxes relative to the page, so a uniformly rescaled page carries them
    exactly as well. On the val split's UCSF documents, 47 of the 53 the fixed
    200 dpi rule rejected were that and nothing else (#41). What stays rejected
    is a page of another shape: a crop, a different page, a page DocILE cut.

    A scale `k` puts the width within the tolerance when it lies in
    `[(width - 1) / w, (width + 1) / w]`, and likewise for the height; one `k`
    serves both when the two intervals overlap. Compared cross-multiplied, so
    whole-point page boxes are compared exactly.
    """
    w, h = points
    if w <= 0 or h <= 0:
        return False
    return (width - TOLERANCE) * h <= (height + TOLERANCE) * w and (
        height - TOLERANCE
    ) * w <= (width + TOLERANCE) * h


def path(directory: Path, document_id: str) -> Path:
    """Where a document's public copy is kept, named by its DocILE id."""
    return directory / f"{document_id}.pdf"


def verify(manifest: Manifest, directory: Path) -> None:
    """Raise unless every pinned copy is in `directory` with its pinned digest.

    What a run does before its first request. A missing or changed copy ends
    the run once, naming the first such document, rather than failing it among
    documents already paid for or scoring a benchmark over a different file.
    """
    _require_digests(manifest)
    for document_id in manifest.document_ids:
        local = path(directory, document_id)
        found = _kept_digest(directory, document_id)
        if found is None:
            raise PublicCopyError(
                f"no public copy of {document_id} at {local}: `docmatch download` "
                "fetches the pinned copies"
            )
        if found != manifest.digests[document_id]:
            raise DigestError(
                f"the public copy of {document_id} at {local} has sha256 {found}, "
                f"but the manifest pins {manifest.digests[document_id]}"
            )


def _require_digests(manifest: Manifest) -> None:
    """Refuse a manifest that pins no digests, since there is nothing to check."""
    if not manifest.digests:
        raise PublicCopyError(
            "the manifest pins no digests, so there is no public copy to verify"
        )


def _kept_digest(directory: Path, document_id: str) -> str | None:
    """The digest of the copy kept in `directory`, or None when none is kept."""
    local = path(directory, document_id)
    return digest(local.read_bytes()) if local.is_file() else None


@dataclass(frozen=True)
class Downloaded:
    """Which pinned copies came from the archive and which were already there."""

    fetched: tuple[str, ...]
    kept: tuple[str, ...]


def download(
    dataset: DocileDataset, manifest: Manifest, directory: Path, fetch: Fetch = fetch
) -> Downloaded:
    """Put every pinned public copy in `directory`, each verified against its digest.

    A copy already there with the pinned digest is kept and the archive is not
    asked. A copy the archive serves with any other digest is not written, and
    ends the download: the benchmark would otherwise quietly read a different
    file.
    """
    _require_digests(manifest)
    directory.mkdir(parents=True, exist_ok=True)
    fetched: list[str] = []
    kept: list[str] = []
    for document_id in manifest.document_ids:
        pinned = manifest.digests[document_id]
        if _kept_digest(directory, document_id) == pinned:
            kept.append(document_id)
            continue
        ucsf_id = dataset.annotation(document_id).metadata.original_filename
        copy = fetch(ucsf_id)
        served = digest(copy)
        if served != pinned:
            raise DigestError(
                f"the archive's copy of {document_id} ({url(ucsf_id)}) has sha256 "
                f"{served}, but the manifest pins {pinned}"
            )
        path(directory, document_id).write_bytes(copy)
        fetched.append(document_id)
    return Downloaded(fetched=tuple(fetched), kept=tuple(kept))
