"""Pinning the documents every number in the README is measured over.

A benchmark is only comparable across commits if the documents behind it do
not move. So the subset is not "100 documents from the val split", it is a
committed list of document ids, and `docmatch subset` re-derives that list
from the split to show it still holds.

Both halves are committed on purpose. The seed alone would leave the subset at
the mercy of the selection code: a change here would quietly draw different
documents and the number would stop meaning what the previous one meant. The
list alone would be a magic file nobody could check. Together, the list is the
subset and the seed is the audit trail.

Document ids are 24-character hashes, and a digest is a hash of a public file.
Neither carries document content or personal data, so committing them
redistributes nothing, which is what rule 6 of `CLAUDE.md` forbids.

Selection
---------

The pool is the split's documents whose DocILE source is `source`, and they are
ranked by `sha256("<seed>:<id>")`, lowest first. That is a seeded draw with
three properties this needs and `random.sample` does not give:

- It does not depend on the interpreter. CPython promises the Mersenne Twister
  stream, not the sampling algorithm on top of it, so a `random.sample` subset
  is reproducible across runs but not guaranteed across Python versions. A
  digest is the same everywhere, forever.
- It does not depend on the order of the split file, or on which other ids are
  in the pool, because each id is hashed alone rather than drawn from a
  sequence. Restricting the pool to one source kept every document of the
  earlier whole-split draw that came from it.
- Drawing fewer ids keeps a prefix of the same documents, so a smaller run is
  still comparable with a larger one.

Admission
---------

Backends read a document's public copy and are scored against DocILE's labels,
which were drawn on DocILE's copy, so a document enters the subset only when
the two copies have the same pages. The ranking is walked in order, each
document admitted or rejected, until `size` are admitted or the pool runs out.

Every reject is pinned with its reason, which is what keeps the check offline:
the ranking minus the rejects, cut at `size`, is the pinned list, and that needs
DocILE's metadata and no network. Every admitted document is pinned with the
sha256 of the public copy that was admitted, so a file that changes in the
archive fails loudly rather than quietly moving the benchmark.
"""

import hashlib
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

SPLIT = "val"
"""The split the subset is drawn from: held out, and the smaller of the two."""

SOURCE = "ucsf"
"""The DocILE source the pool is restricted to.

The one archive whose documents can be fetched by id. A hosted backend may not
read DocILE's own copies (#16), and FCC documents cannot be resolved to a file
from what DocILE records (#26).
"""

SIZE = 100
"""How many documents the subset holds, when that many are admitted."""

SEED = 20260912
"""The seed that drew the committed subset. Changing it draws a new benchmark."""

MANIFEST = Path(__file__).parent / "subset.json"
"""The committed manifest, beside the code that reads it rather than in `data/`,
which is ignored."""

Reason = Literal["page count differs", "page size differs", "fetch failed"]
"""Why a document was left out: its public copy does not have DocILE's pages,
or there was no public copy to compare."""


class ManifestError(Exception):
    """The manifest cannot be read, or is not a manifest."""


class Rejected(Exception):
    """A document failed admission, for a reason the manifest records."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


class Manifest(BaseModel):
    """The fixed subset: which documents, drawn how, and what was left out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    split: str
    seed: int
    source: str
    size: int
    document_ids: tuple[str, ...]
    rejected: dict[str, Reason] = {}
    """Every document the walk left out, in ranking order, with its reason."""
    digests: dict[str, str] = {}
    """The sha256 of each pinned document's public copy, hex. Empty for a
    corpus that is only ever scored and never read, like the synthetic one."""

    @model_validator(mode="after")
    def _a_consistent_pin(self) -> "Manifest":
        repeated = len(self.document_ids) - len(set(self.document_ids))
        if repeated:
            raise ValueError(f"{repeated} document ids are listed more than once")
        if self.size != len(self.document_ids):
            raise ValueError(
                f"size is {self.size} but {len(self.document_ids)} ids are listed"
            )
        both = set(self.document_ids) & set(self.rejected)
        if both:
            raise ValueError(f"{len(both)} document ids are both pinned and rejected")
        if self.digests and set(self.digests) != set(self.document_ids):
            digested = len(set(self.digests) & set(self.document_ids))
            raise ValueError(
                f"digests cover {digested} of the {self.size} documents, and "
                "digest nothing else"
            )
        return self

    def first(self, size: int) -> "Manifest":
        """The first `size` documents of this subset, as a subset of their own.

        A prefix is a sample of the same draw, which is the third property the
        ranking was built for, so a run over the first ten documents is a run
        over ten documents this seed chose and not over ten arbitrary ones. It
        is for checking that a run works before paying for the whole subset; a
        benchmark row is measured over the manifest itself.
        """
        if not 0 < size <= self.size:
            raise ManifestError(
                f"cannot take the first {size} of a subset of {self.size}"
            )
        kept = self.document_ids[:size]
        return self.model_copy(
            update={
                "size": size,
                "document_ids": kept,
                "digests": {
                    document_id: self.digests[document_id]
                    for document_id in kept
                    if document_id in self.digests
                },
            }
        )

    def reproduced_from(self, pool: Iterable[str]) -> tuple[str, ...]:
        """What this manifest's seed draws from a pool, minus what it rejected.

        The pool is the split's documents from this manifest's source; picking
        them out takes DocILE's metadata, which is the caller's to read.
        """
        admitted = (
            each for each in rank(pool, seed=self.seed) if each not in self.rejected
        )
        return tuple(admitted)[: self.size]


def rank(document_ids: Iterable[str], *, seed: int) -> tuple[str, ...]:
    """Every distinct id, in the order this seed draws them, lowest digest first."""
    return tuple(
        sorted(set(document_ids), key=lambda each: (_digest(seed, each), each))
    )


def select(document_ids: Iterable[str], *, seed: int, size: int) -> tuple[str, ...]:
    """The `size` ids that this seed draws first."""
    ranked = rank(document_ids, seed=seed)
    if len(ranked) < size:
        raise ManifestError(
            f"cannot draw {size} documents from a split of {len(ranked)}"
        )
    return ranked[:size]


def _digest(seed: int, document_id: str) -> bytes:
    return hashlib.sha256(f"{seed}:{document_id}".encode()).digest()


def admitted(
    pool: Iterable[str],
    *,
    split: str,
    seed: int,
    source: str,
    size: int,
    admit: Callable[[str], str],
) -> Manifest:
    """A manifest of the first `size` documents of the ranking that `admit` passes.

    `admit` returns the digest of the document's public copy, or raises
    `Rejected`. It is asked about no document past the last one needed, since
    each question is a request to the archive. Fewer than `size` admitted is
    still a subset, of every document that passed; none is not.
    """
    digests: dict[str, str] = {}
    rejected: dict[str, Reason] = {}
    for document_id in rank(pool, seed=seed):
        if len(digests) == size:
            break
        try:
            digests[document_id] = admit(document_id)
        except Rejected as reject:
            rejected[document_id] = reject.reason
    if not digests:
        reasons = Counter(rejected.values())
        counted = ", ".join(f"{count} {reason}" for reason, count in reasons.items())
        raise ManifestError(
            f"no document was admitted: {counted or 'the pool is empty'}"
        )
    return Manifest(
        split=split,
        seed=seed,
        source=source,
        size=len(digests),
        document_ids=tuple(digests),
        rejected=rejected,
        digests=digests,
    )


def load(path: Path = MANIFEST) -> Manifest:
    """The manifest at this path, or a message saying what is wrong with it."""
    try:
        return Manifest.model_validate_json(path.read_bytes())
    except OSError as error:
        raise ManifestError(f"cannot read the manifest {path}: {error}") from error
    except ValidationError as error:
        raise ManifestError(
            f"{path} is not a manifest: expected a JSON object with a split, a "
            f"seed, a source, a size, that many distinct document ids, and "
            f"optionally the rejected ids and the digests. {_first_problem(error)}"
        ) from error


def _first_problem(error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    named = f"{where} is the first problem" if where else "The file itself"
    return f"{named}: {first['msg'].lower()}."


def write(manifest: Manifest, path: Path = MANIFEST) -> None:
    """Write the manifest as a diff-readable JSON object, one id per line."""
    body = manifest.model_dump_json(indent=2)
    path.write_text(f"{body}\n", encoding="utf-8")
