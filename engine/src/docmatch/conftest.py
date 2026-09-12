"""Fixtures shared by the tests in this package.

DocILE may not be redistributed, so every test builds what it needs from one
committed synthetic annotation rather than from the real dataset. Nothing
here touches `data/`, which is how the suite stays runnable in CI.
"""

import shutil
from pathlib import Path

import pytest

from docmatch.docile.annotation import Annotation

FIXTURES = Path(__file__).parent / "docile" / "fixtures"
SYNTHETIC_ANNOTATION = FIXTURES / "synthetic_annotation.json"
SYNTHETIC_HEADER_ONLY = FIXTURES / "synthetic_header_only.json"


@pytest.fixture
def synthetic_annotation() -> Annotation:
    """The labels of the synthetic document, parsed."""
    return Annotation.model_validate_json(SYNTHETIC_ANNOTATION.read_bytes())


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A DocILE dataset directory holding two synthetic documents.

    `syn0001` has a line-item table; `syn0002` is header only, which 355 of
    the 5,680 real annotated documents are. A `val` split lists both, in the
    order a split file's order means nothing.
    """
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    shutil.copy(SYNTHETIC_ANNOTATION, annotations / "syn0001.json")
    shutil.copy(SYNTHETIC_HEADER_ONLY, annotations / "syn0002.json")
    split = tmp_path / "val.json"
    split.write_text('["syn0002", "syn0001"]', encoding="utf-8")
    return tmp_path


SYNTHETIC_SUBSET = Path(__file__).parents[2] / "tests" / "evals" / "synthetic"


@pytest.fixture
def synthetic_subset() -> Path:
    """The committed corpus the eval command runs on in CI.

    A dataset in DocILE's shape, a manifest drawn from it, and a predictions
    file covering all but one of the pinned documents plus one the manifest
    does not pin. No real document content, so it is committed and CI needs no
    dataset.
    """
    return SYNTHETIC_SUBSET
