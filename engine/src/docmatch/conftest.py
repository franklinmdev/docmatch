"""Fixtures shared by the tests in this package.

DocILE may not be redistributed, so every test builds what it needs from one
committed synthetic annotation rather than from the real dataset. Nothing
here touches `data/`, which is how the suite stays runnable in CI.
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from docmatch.docile.annotation import Annotation
from docmatch.resolution.catalog import ResolutionError
from docmatch.resolution.store import Store, connect, resolve_database_url

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
    the 5,680 real annotated documents are, as `docmatch corpus` counts them.
    A `val` split lists both, in the order a split file's order means nothing.
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
    does not pin. Beside the predictions, a `manifest.json` and a `run.json`,
    so the directory is also a saved run `docmatch match --run` takes. No
    real document content, so it is committed and CI needs no dataset.
    """
    return SYNTHETIC_SUBSET


TEST_SCHEMA = "resolution_test"
"""Where the tests build their catalogs, so a run's `resolution` schema is
left for inspection."""


@pytest.fixture
def database_url() -> str:
    """The database the resolution tests run against, at the command's own
    precedence, or a skip when none answers: CI has the container and the
    named machine has the socket, and a machine without either is not a
    broken engine, the corpus pattern."""
    url = resolve_database_url(None)
    try:
        connect(url).close()
    except ResolutionError as error:
        pytest.skip(str(error))
    return url


@pytest.fixture
def store(database_url: str) -> Iterator[Store]:
    """One connection to that database, on the test schema, closed after."""
    connection = connect(database_url)
    try:
        yield Store(connection, TEST_SCHEMA)
    finally:
        connection.close()
