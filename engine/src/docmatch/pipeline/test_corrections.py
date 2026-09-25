"""Tests for exporting a schema's settled corrections: reviewed through the
HTTP API against a real Postgres, skipped when none answers, with replay
answering from a saved run."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docmatch.evals.corrections import (
    CorrectionsError,
    Export,
    read_corrections,
    reading_digest,
)
from docmatch.evals.manifest import load as load_manifest
from docmatch.pipeline.conftest import TEST_SCHEMA, case_text, ordered, pdf, settle
from docmatch.pipeline.conftest import uploaded as upload
from docmatch.pipeline.corrections import export, write
from docmatch.pipeline.loop import Resolve, settled
from docmatch.pipeline.replay import Replay
from docmatch.pipeline.store import Connection


def edit(client: TestClient, document: int, body: dict[str, object]) -> None:
    response = client.post(f"/documents/{document}/edits", json=body)
    assert response.status_code == 200, response.text


def decide(client: TestClient, document: int, decision: str) -> None:
    response = client.post(
        f"/documents/{document}/decision", json={"decision": decision}
    )
    assert response.status_code == 200, response.text


def test_the_export_holds_what_the_reviewer_changed_and_the_lines_left(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    saved_run: Path,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    """eval0003 approved with a header value and a cell put right, eval0005
    rejected with a line removed, one approved untouched and one still in
    review: only the two decided with a correction leave, and of them what
    was changed, and, since a line was corrected on each, every line as
    left to pair them by."""
    totals = upload(client, pdf(tmp_path, "eval0003"), case_text(ordered("eval0003")))
    held = upload(
        client,
        pdf(tmp_path, "eval0005"),
        case_text(ordered("eval0005", billed_above=True)),
    )
    untouched = upload(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    open_one = upload(client, pdf(tmp_path, "eval0003"), case_text(ordered("eval0005")))
    assert settle(connection, replayed, resolver).count("approved") == 1
    assert client.get(f"/documents/{untouched}").json()["status"] == "approved"
    edit(
        client,
        totals,
        {"kind": "header", "fieldtype": "amount_due", "value": "$412.50"},
    )
    edit(
        client,
        totals,
        {"kind": "cell", "line": 2, "fieldtype": "line_item_quantity", "value": "1"},
    )
    decide(client, totals, "approved")
    edit(client, held, {"kind": "line removed", "line": 2})
    decide(client, held, "rejected")
    edit(
        client,
        open_one,
        {"kind": "header", "fieldtype": "vendor_name", "value": "Lakeside"},
    )

    exported = export(
        TEST_SCHEMA, settled(connection), load_manifest(saved_run / "manifest.json")
    )

    readings = replayed.predictions
    assert exported == Export.model_validate(
        {
            "schema": TEST_SCHEMA,
            "documents": [
                {
                    "document_id": "eval0003",
                    "reading": reading_digest(readings["eval0003"]),
                    "decision": "approved",
                    "corrections": [
                        {
                            "kind": "header",
                            "fieldtype": "amount_due",
                            "left": ["$412.50"],
                        },
                        {
                            "kind": "cell",
                            "line": 2,
                            "fieldtype": "line_item_quantity",
                            "left": ["1"],
                        },
                    ],
                    "lines": {
                        "0": {
                            "line_item_quantity": ["5"],
                            "line_item_description": ["Work gloves"],
                            "line_item_amount_gross": ["50.00"],
                        },
                        "1": {
                            "line_item_quantity": ["3"],
                            "line_item_description": ["Hex key set"],
                            "line_item_amount_gross": ["45.00"],
                        },
                        "2": {
                            "line_item_description": ["Torque wrench"],
                            "line_item_amount_gross": ["317.50"],
                            "line_item_quantity": ["1"],
                        },
                    },
                },
                {
                    "document_id": "eval0005",
                    "reading": reading_digest(readings["eval0005"]),
                    "decision": "rejected",
                    "corrections": [
                        {
                            "kind": "line removed",
                            "line": 2,
                            "read": {
                                "line_item_quantity": ["1"],
                                "line_item_description": ["Delivery"],
                                "line_item_amount_gross": ["0.00"],
                            },
                        }
                    ],
                    "lines": {
                        "0": {
                            "line_item_quantity": ["10"],
                            "line_item_description": ["Cable reel, 25 m"],
                            "line_item_amount_gross": ["900.00"],
                        },
                        "1": {
                            "line_item_quantity": ["4"],
                            "line_item_description": ["Junction box"],
                            "line_item_amount_gross": ["42.00"],
                        },
                    },
                },
            ],
        }
    )


def test_the_export_is_written_as_the_eval_reads_it(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    saved_run: Path,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = upload(client, pdf(tmp_path, "eval0003"), case_text(ordered("eval0003")))
    settle(connection, replayed, resolver)
    edit(
        client,
        document,
        {"kind": "header", "fieldtype": "amount_due", "value": "$412.50"},
    )
    decide(client, document, "approved")
    exported = export(
        TEST_SCHEMA, settled(connection), load_manifest(saved_run / "manifest.json")
    )
    out = tmp_path / "data" / "corrections" / f"{TEST_SCHEMA}.json"

    write(exported, out)

    assert read_corrections(out) == exported
    assert exported.documents[0].lines == {}, "only the header was corrected"
    assert json.loads(out.read_text("utf-8"))["schema"] == TEST_SCHEMA


def test_a_corrected_document_the_manifest_does_not_pin_is_reported(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    saved_run: Path,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = upload(client, pdf(tmp_path, "eval0003"), case_text(ordered("eval0003")))
    settle(connection, replayed, resolver)
    edit(
        client,
        document,
        {"kind": "header", "fieldtype": "amount_due", "value": "$412.50"},
    )
    decide(client, document, "approved")
    pinned = load_manifest(saved_run / "manifest.json")
    elsewhere = pinned.model_copy(
        update={"digests": {**pinned.digests, "eval0003": "0" * 64}}
    )

    with pytest.raises(CorrectionsError, match=f"document {document} of schema"):
        export(TEST_SCHEMA, settled(connection), elsewhere)
