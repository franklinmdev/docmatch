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
    the 5,680 real annotated documents are.
    """
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    shutil.copy(SYNTHETIC_ANNOTATION, annotations / "syn0001.json")
    shutil.copy(SYNTHETIC_HEADER_ONLY, annotations / "syn0002.json")
    return tmp_path
