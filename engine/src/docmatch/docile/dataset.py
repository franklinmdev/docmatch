"""Reading a downloaded DocILE dataset from disk.

The dataset is licensed for non-commercial research, is never committed, and
CI never sees it. Only this loader and the numbers derived from it are public.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from docmatch.docile.annotation import Annotation


class DocileError(Exception):
    """Something is wrong with the dataset directory or what was asked of it."""


class InvalidDocumentIdError(DocileError, ValueError):
    """The string given is not a document id and must not become a path."""


class DatasetNotFoundError(DocileError):
    """There is no dataset at this path; it was probably never downloaded."""


class DocumentNotFoundError(DocileError):
    """The dataset holds no annotation for this document id."""


DOCUMENT_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
"""DocILE ids are hex strings; this stays wider, but never spans directories."""


@dataclass(frozen=True)
class DocileDataset:
    """A downloaded DocILE dataset directory."""

    root: Path

    def annotation(self, document_id: str) -> Annotation:
        """The labels for one document."""
        if not DOCUMENT_ID.match(document_id):
            raise InvalidDocumentIdError(f"not a document id: {document_id!r}")
        annotations = self.root / "annotations"
        if not annotations.is_dir():
            raise DatasetNotFoundError(
                f"no DocILE dataset at {self.root}: expected {annotations} to exist. "
                "See the Data section of README.md for how to download it."
            )
        path = annotations / f"{document_id}.json"
        if not path.is_file():
            raise DocumentNotFoundError(
                f"no annotation for document {document_id!r} in {self.root}"
            )
        return Annotation.model_validate_json(path.read_bytes())
