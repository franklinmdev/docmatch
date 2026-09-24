"""Fixtures for the loop tests: a saved run to replay, cases, and a schema.

No vendor is called and no model is loaded. The saved run is the committed
synthetic fixture's readings, each pinned to a PDF these helpers write, with
a `run.json` saying what each document cost, so replay answers exactly as a
backend once did. The database is the resolution tests' own, skipped when
none answers.
"""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docmatch.evals.public import digest
from docmatch.evals.run import read_predictions
from docmatch.extraction.conftest import write_pdf
from docmatch.matching.records import ReceiptLine, ReceivingRecord, Record
from docmatch.metrics.fields import FieldValues
from docmatch.pipeline import api, replay
from docmatch.pipeline.case import Case, case_json
from docmatch.pipeline.replay import Replay
from docmatch.pipeline.store import Connection, drop_schema, open_schema, prepare
from docmatch.resolution.store import connect

SYNTHETIC = Path(__file__).parents[3] / "tests" / "evals" / "synthetic"

TEST_SCHEMA = "pipeline_test"

SAVED = {
    "eval0003": {"cost": "0.00211", "input_tokens": 1800, "output_tokens": 240},
    "eval0005": {"cost": "0.00203", "input_tokens": 1750, "output_tokens": 230},
    "eval0006": {
        "cost": "0.00052",
        "input_tokens": 400,
        "output_tokens": 30,
        "failure": "answer did not fit the schema",
    },
}
"""What the saved run says each document cost, in all. eval0003's totals
disagree, eval0005's reading passes the gate, and eval0006 has no reading."""


@pytest.fixture
def saved_run(tmp_path: Path) -> Path:
    return write_saved_run(tmp_path)


def write_saved_run(tmp_path: Path) -> Path:
    """A saved run directory replay reads: the fixture's readings, one PDF
    per document pinned by digest, and a record of each one's cost. The PDFs
    are written beside it."""
    directory = tmp_path / "run"
    directory.mkdir()
    shutil.copy(SYNTHETIC / "predictions.json", directory / "predictions.json")
    digests = {
        document_id: digest(pdf(tmp_path, document_id).read_bytes())
        for document_id in SAVED
    }
    manifest = {
        "split": "val",
        "seed": 1,
        "source": "synthetic",
        "size": len(SAVED),
        "document_ids": list(SAVED),
        "rejected": {},
        "digests": digests,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    record = {
        "backend": "gemini",
        "requested_model": "synthetic-001",
        "documents": [
            {"document_id": document_id, "served_model": "synthetic-001-a", **saved}
            for document_id, saved in SAVED.items()
        ],
    }
    (directory / "run.json").write_text(json.dumps(record), "utf-8")
    return directory


def pdf(directory: Path, document_id: str) -> Path:
    """A one-page PDF standing for the document, its width telling it apart."""
    width = 600 + int(document_id[-2:])
    return write_pdf(directory / f"{document_id}.pdf", width=width)


@pytest.fixture
def replayed(saved_run: Path) -> Replay:
    return replay.load(saved_run)


def ordered(document_id: str, *, billed_above: bool = False) -> Case:
    """A case whose purchase order lists the reading's own lines, each fully
    received, so matching finds nothing; `billed_above` puts the first line's
    amount a fifth below the invoice's, an injected price variance."""
    rows = read_predictions(SYNTHETIC / "predictions.json")[document_id].rows
    lines: list[FieldValues] = [dict(row) for row in rows]
    if billed_above:
        first = dict(lines[0])
        amount = float(first["line_item_amount_gross"][0])
        first["line_item_amount_gross"] = (f"{amount * 0.8:.2f}",)
        lines[0] = first
    receipt = tuple(
        ReceiptLine({"line_item_quantity": row["line_item_quantity"]}, index)
        for index, row in enumerate(lines)
        if "line_item_quantity" in row
    )
    return Case(Record(header={}, lines=tuple(lines)), ReceivingRecord(receipt))


def case_text(case: Case) -> str:
    return json.dumps(case_json(case))


@pytest.fixture
def schema(database_url: str) -> Iterator[str]:
    """The test schema, dropped and prepared afresh, so no test sees another's
    documents."""
    with connect(database_url) as connection:
        connection.autocommit = True
        drop_schema(connection, TEST_SCHEMA)
    prepare(database_url, TEST_SCHEMA)
    yield TEST_SCHEMA


@pytest.fixture
def connection(database_url: str, schema: str) -> Iterator[Connection]:
    opened = open_schema(database_url, schema)
    try:
        yield opened
    finally:
        opened.close()


@pytest.fixture
def client(database_url: str, schema: str) -> Iterator[TestClient]:
    with TestClient(api.create(database_url, schema)) as client:
        yield client
