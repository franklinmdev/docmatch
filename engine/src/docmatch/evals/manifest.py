"""Pinning the documents every number in the README is measured over.

A benchmark is only comparable across commits if the documents behind it do
not move. So the subset is not "100 documents from the val split", it is a
committed list of 100 document ids, and `docmatch subset` re-derives that list
from the split to show it still holds.

Both halves are committed on purpose. The seed alone would leave the subset at
the mercy of the selection code: a change here would quietly draw different
documents and the number would stop meaning what the previous one meant. The
list alone would be a magic file nobody could check. Together, the list is the
subset and the seed is the audit trail.

Document ids are 24-character hashes. They carry no document content and no
personal data, so committing them redistributes nothing, which is what rule 6
of `CLAUDE.md` forbids.

Selection
---------

The subset is the `size` ids whose `sha256("<seed>:<id>")` sorts first. That is
a seeded draw with three properties this needs and `random.sample` does not
give:

- It does not depend on the interpreter. CPython promises the Mersenne Twister
  stream, not the sampling algorithm on top of it, so a `random.sample` subset
  is reproducible across runs but not guaranteed across Python versions. A
  digest is the same everywhere, forever.
- It does not depend on the order of the split file, because each id is hashed
  alone rather than drawn from a sequence.
- Drawing fewer ids keeps a prefix of the same documents, so a smaller run is
  still comparable with a larger one.
"""

import hashlib
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

SPLIT = "val"
"""The split the subset is drawn from: held out, and the smaller of the two."""

SIZE = 100
"""How many documents the subset holds."""

SEED = 20260912
"""The seed that drew the committed subset. Changing it draws a new benchmark."""

MANIFEST = Path(__file__).parent / "subset.json"
"""The committed manifest, beside the code that reads it rather than in `data/`,
which is ignored."""


class ManifestError(Exception):
    """The manifest cannot be read, or is not a manifest."""


class Manifest(BaseModel):
    """The fixed subset: which documents, from which split, and how they were drawn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    split: str
    seed: int
    size: int
    document_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _size_is_the_number_of_distinct_ids(self) -> "Manifest":
        repeated = len(self.document_ids) - len(set(self.document_ids))
        if repeated:
            raise ValueError(f"{repeated} document ids are listed more than once")
        if self.size != len(self.document_ids):
            raise ValueError(
                f"size is {self.size} but {len(self.document_ids)} ids are listed"
            )
        return self

    def reproduced_from(self, document_ids: Iterable[str]) -> tuple[str, ...]:
        """The subset this manifest's own seed and size draw from a split."""
        return select(document_ids, seed=self.seed, size=self.size)


def select(document_ids: Iterable[str], *, seed: int, size: int) -> tuple[str, ...]:
    """The `size` ids that this seed draws, lowest digest first."""
    distinct = set(document_ids)
    if len(distinct) < size:
        raise ManifestError(
            f"cannot draw {size} documents from a split of {len(distinct)}"
        )
    ordered = sorted(distinct, key=lambda each: (_digest(seed, each), each))
    return tuple(ordered[:size])


def _digest(seed: int, document_id: str) -> bytes:
    return hashlib.sha256(f"{seed}:{document_id}".encode()).digest()


def selected(
    document_ids: Iterable[str], *, split: str, seed: int, size: int
) -> Manifest:
    """A manifest for the subset this seed draws from a split."""
    return Manifest(
        split=split,
        seed=seed,
        size=size,
        document_ids=select(document_ids, seed=seed, size=size),
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
            f"seed, a size, and that many distinct document ids. "
            f"{_first_problem(error)}"
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
