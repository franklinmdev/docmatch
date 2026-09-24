"""Tests for the loop, through the HTTP API and the worker's claim-and-advance.

Against a real Postgres, skipped when none answers, with replay answering
from a saved run: no vendor is called and no model is loaded.
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from docmatch.extraction.conftest import write_pdf
from docmatch.pipeline.conftest import SAVED, case_text, ordered, pdf
from docmatch.pipeline.loop import (
    Refused,
    Status,
    advance,
    claim,
    claim_and_advance,
)
from docmatch.pipeline.replay import Replay
from docmatch.pipeline.store import Connection


def uploaded(client: TestClient, invoice: Path, case: str, expected: int = 201) -> int:
    response = client.post(
        "/documents",
        files={"invoice": ("invoice.pdf", invoice.read_bytes(), "application/pdf")},
        data={"case": case},
    )
    assert response.status_code == expected, response.text
    document = response.json()["id"]
    assert isinstance(document, int)
    return document


def settle(connection: Connection, replayed: Replay) -> list[Status]:
    """Every status the worker moves a document to until nothing is pending."""
    moved = []
    while (
        status := claim_and_advance(connection, replayed, wait=_no_wait)
    ) is not None:
        moved.append(status)
    return moved


def _no_wait(_: float) -> None:
    pass


def test_a_clean_case_is_approved_by_the_system(
    client: TestClient, connection: Connection, replayed: Replay, tmp_path: Path
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )

    assert settle(connection, replayed) == [
        "extracted",
        "validated",
        "resolved",
        "matched",
        "approved",
    ]
    view = client.get(f"/documents/{document}").json()
    assert view["status"] == "approved"
    assert view["routing_reasons"] == []
    assert view["gate"]["verdict"] == "passed"
    assert view["match"]["verdict"] == "approvable"
    last = client.get(f"/documents/{document}/trace").json()["transitions"][-1]
    assert (last["from"], last["to"], last["actor"]) == (
        "matched",
        "approved",
        "system",
    )


def test_an_injected_hold_goes_to_review_with_match_held(
    client: TestClient, connection: Connection, replayed: Replay, tmp_path: Path
) -> None:
    case = ordered("eval0005", billed_above=True)
    document = uploaded(client, pdf(tmp_path, "eval0005"), case_text(case))

    settle(connection, replayed)

    view = client.get(f"/documents/{document}").json()
    assert view["status"] == "needs_review"
    assert view["routing_reasons"] == ["match held"]
    (finding,) = view["match"]["findings"]
    assert finding["type"] == "price variance"
    assert finding["severity"] == "hold"
    assert finding["explanation"].startswith("price variance, PO line 0, amount")


def test_totals_that_disagree_go_to_review_with_gate_failed(
    client: TestClient, connection: Connection, replayed: Replay, tmp_path: Path
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0003"), case_text(ordered("eval0003"))
    )

    settle(connection, replayed)

    view = client.get(f"/documents/{document}").json()
    assert view["status"] == "needs_review"
    assert view["routing_reasons"] == ["gate failed"]
    assert view["match"]["verdict"] == "approvable"


def test_a_document_with_no_reading_goes_to_review_from_received(
    client: TestClient, connection: Connection, replayed: Replay, tmp_path: Path
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0006"), case_text(ordered("eval0005"))
    )

    assert settle(connection, replayed) == ["needs_review"]
    view = client.get(f"/documents/{document}").json()
    assert view["routing_reasons"] == ["extraction failed"]
    assert view["reading"] is None
    moves = [
        (each["from"], each["to"])
        for each in client.get(f"/documents/{document}/trace").json()["transitions"]
    ]
    assert moves == [(None, "received"), ("received", "needs_review")]


def test_a_pdf_the_saved_run_does_not_pin_fails_extraction(
    client: TestClient, connection: Connection, replayed: Replay, tmp_path: Path
) -> None:
    unknown = write_pdf(tmp_path / "unknown.pdf", width=500)
    document = uploaded(client, unknown, case_text(ordered("eval0005")))

    settle(connection, replayed)

    view = client.get(f"/documents/{document}").json()
    assert view["routing_reasons"] == ["extraction failed"]
    assert view["cost"] == "0"


def test_a_second_transition_from_the_same_status_is_refused(
    client: TestClient, connection: Connection, replayed: Replay, tmp_path: Path
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    claim_and_advance(connection, replayed, wait=_no_wait)
    taken = claim(connection)
    assert taken is not None and taken.status == "extracted"

    advance(connection, taken, replayed)
    with pytest.raises(Refused):
        advance(connection, taken, replayed)

    moves = [
        (each["from"], each["to"])
        for each in client.get(f"/documents/{document}/trace").json()["transitions"]
    ]
    assert moves == [
        (None, "received"),
        ("received", "extracted"),
        ("extracted", "validated"),
    ]


def test_transitions_are_append_only(
    client: TestClient, connection: Connection, replayed: Replay, tmp_path: Path
) -> None:
    uploaded(client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005")))
    settle(connection, replayed)

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        connection.execute("UPDATE transitions SET actor = 'reviewer'")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        connection.execute("DELETE FROM transitions")


def test_a_repeated_upload_returns_the_same_document_read_once(
    client: TestClient, connection: Connection, replayed: Replay, tmp_path: Path
) -> None:
    invoice, case = pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    document = uploaded(client, invoice, case)
    settle(connection, replayed)
    reading = client.get(f"/documents/{document}").json()["reading"]

    respaced = json.dumps(json.loads(case), indent=2)
    again = uploaded(client, invoice, respaced, expected=200)
    settle(connection, replayed)

    assert again == document
    assert client.get(f"/documents/{document}").json()["reading"] == reading
    assert len(client.get(f"/documents/{document}/trace").json()["vendor_calls"]) == 1


def test_the_same_pdf_with_another_case_is_another_document(
    client: TestClient, tmp_path: Path
) -> None:
    invoice = pdf(tmp_path, "eval0005")
    first = uploaded(client, invoice, case_text(ordered("eval0005")))
    other = case_text(ordered("eval0005", billed_above=True))

    assert uploaded(client, invoice, other) != first


def test_a_bad_pdf_is_refused_at_upload(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/documents",
        files={"invoice": ("invoice.pdf", b"not a pdf", "application/pdf")},
        data={"case": case_text(ordered("eval0005"))},
    )

    assert response.status_code == 422
    assert "not a PDF" in response.json()["detail"]


@pytest.mark.parametrize(
    "case",
    [
        "not json",
        '{"purchase_order": {"header": {}, "lines": []}}',
        (
            '{"purchase_order": {"header": {}, "lines": [{}]}, "receipt": {"lines": '
            '[{"cells": {}, "po_line": 0}, {"cells": {}, "po_line": 0}]}}'
        ),
    ],
)
def test_a_bad_case_is_refused_at_upload(
    client: TestClient, tmp_path: Path, case: str
) -> None:
    response = client.post(
        "/documents",
        files={
            "invoice": (
                "i.pdf",
                pdf(tmp_path, "eval0005").read_bytes(),
                "application/pdf",
            )
        },
        data={"case": case},
    )

    assert response.status_code == 422
    assert "the case is not" in response.json()["detail"]


@pytest.mark.parametrize("document_id", ["eval0005", "eval0006"])
def test_cost_per_document_is_the_sum_of_its_vendor_calls_and_the_source_runs(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    document_id: str,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, document_id), case_text(ordered("eval0005"))
    )
    settle(connection, replayed)

    calls = client.get(f"/documents/{document}/trace").json()["vendor_calls"]
    cost = client.get(f"/documents/{document}").json()["cost"]

    assert Decimal(cost) == sum(Decimal(each["cost"]) for each in calls)
    assert Decimal(cost) == Decimal(str(SAVED[document_id]["cost"]))
    (call,) = calls
    assert call["status"] == "received"
    assert call["served_model"] == (
        "synthetic-001-a" if document_id == "eval0005" else None
    )
    assert call["failed"] == (document_id == "eval0006")
    assert call["units"]["input_tokens"] == SAVED[document_id]["input_tokens"]


def test_transitions_record_when_work_was_taken_up_and_committed(
    client: TestClient, connection: Connection, replayed: Replay, tmp_path: Path
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    settle(connection, replayed)

    upload, *moves = client.get(f"/documents/{document}/trace").json()["transitions"]

    assert upload["taken_at"] is None
    previous = datetime.fromisoformat(upload["committed_at"])
    for each in moves:
        taken = datetime.fromisoformat(each["taken_at"])
        committed = datetime.fromisoformat(each["committed_at"])
        assert previous <= taken <= committed
        previous = committed
    latency = client.get(f"/documents/{document}").json()["latency"]
    assert latency == pytest.approx(
        (previous - datetime.fromisoformat(upload["committed_at"])).total_seconds()
    )


def test_an_unknown_document_is_not_found(client: TestClient) -> None:
    assert client.get("/documents/999").status_code == 404
    assert client.get("/documents/999/trace").status_code == 404
