"""Reading a downloaded DocILE dataset from disk.

The dataset is licensed for non-commercial research, is never committed, and
CI never sees it. Only this loader and the numbers derived from it are public.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from docmatch.docile.annotation import Annotation


class DocileError(Exception):
    """Something is wrong with the dataset directory or what was asked of it."""


class InvalidNameError(DocileError, ValueError):
    """The string given is not a name and must not become a path."""


class DatasetNotFoundError(DocileError):
    """There is no dataset at this path; it was probably never downloaded."""


class DocumentNotFoundError(DocileError):
    """The dataset holds no annotation for this document id."""


class SplitNotFoundError(DocileError):
    """The dataset holds no split by this name."""


class InvalidSplitError(DocileError):
    """The split file is there but is not a list of document ids."""


NAME = re.compile(r"[A-Za-z0-9_-]+\Z")
"""DocILE ids and split names are plain; this stays wider, but never spans
directories."""

DOCUMENT_IDS = TypeAdapter(tuple[str, ...])
"""A split file is a JSON array of document ids, and nothing else.

Built once and reused: "the provided type must be analyzed and converted into a
pydantic-core schema. This comes with some non-trivial overhead, so it is
recommended to create a TypeAdapter for a given type just once" (pydantic
2.13.5, docs read 2026-09-12).
"""


@dataclass(frozen=True)
class DocileDataset:
    """A downloaded DocILE dataset directory."""

    root: Path

    def annotation(self, document_id: str) -> Annotation:
        """The labels for one document."""
        path = self._annotations() / f"{_name(document_id)}.json"
        if not path.is_file():
            raise DocumentNotFoundError(
                f"no annotation for document {document_id!r} in {self.root}"
            )
        return Annotation.model_validate_json(path.read_bytes())

    def document_ids(self, split: str) -> tuple[str, ...]:
        """Every document id in one split, in the order the split file lists them.

        DocILE ships `train`, `val` and their union `trainval`. The order is
        the dataset's own and carries no meaning, so anything that has to stay
        reproducible sorts the ids itself rather than trusting it.
        """
        self._annotations()  # the same "never downloaded" error, before the split
        path = self.root / f"{_name(split)}.json"
        if not path.is_file():
            raise SplitNotFoundError(f"no split {path.name} in {self.root}")
        try:
            document_ids = DOCUMENT_IDS.validate_json(path.read_bytes())
        except ValidationError as error:
            raise InvalidSplitError(
                f"{path} is not a split: expected a JSON array of document ids"
            ) from error
        for document_id in document_ids:
            _name(document_id)
        return document_ids

    def _annotations(self) -> Path:
        """The annotations directory, which is what a downloaded dataset has."""
        annotations = self.root / "annotations"
        if not annotations.is_dir():
            raise DatasetNotFoundError(
                f"no DocILE dataset at {self.root}: expected {annotations} to exist. "
                "See the Data section of README.md for how to download it."
            )
        return annotations


def _name(name: str) -> str:
    """A document id or a split name, once it is certain it is only that."""
    if not NAME.match(name):
        raise InvalidNameError(f"not a document id or split name: {name!r}")
    return name
