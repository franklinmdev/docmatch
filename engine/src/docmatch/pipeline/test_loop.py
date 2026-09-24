"""Tests for the loop, through the HTTP API and the worker's claim-and-advance.

Against a real Postgres, skipped when none answers, with replay answering
from a saved run: no vendor is called and no model is loaded.
"""

import json
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from docmatch.extraction.conftest import write_pdf
from docmatch.pipeline import api, loop
from docmatch.pipeline.conftest import CATALOG, SAVED, case_text, ordered, pdf
from docmatch.pipeline.loop import (
    LAPSES,
    Claim,
    Refused,
    Resolve,
    Status,
    advance,
    claim,
    claim_and_advance,
)
from docmatch.pipeline.replay import Replay
from docmatch.pipeline.store import Connection, drop_schema, open_schema, prepare
from docmatch.resolution.arms import RRF_K
from docmatch.resolution.catalog import mint
from docmatch.resolution.operating import Resolved
from docmatch.resolution.store import connect


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


def settle(connection: Connection, replayed: Replay, resolver: Resolve) -> list[Status]:
    """Every status the worker moves a document to until nothing is pending."""
    moved = []
    while (
        status := claim_and_advance(connection, replayed, resolver, wait=_no_wait)
    ) is not None:
        moved.append(status)
    return moved


def _no_wait(_: float) -> None:
    pass


def test_a_clean_case_is_approved_by_the_system(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )

    assert settle(connection, replayed, resolver) == [
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


def test_every_line_is_resolved_and_one_in_the_catalog_carries_its_sku(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    """eval0005 reads three lines, the first two catalog entries word for
    word, so both are first in both halves. `Delivery` is out of catalog:
    no entry is first in one half and second in the other, so its best is
    first in one and third in the other, below the operating threshold, and
    it has no entry but keeps its score."""
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )

    settle(connection, replayed, resolver)

    first, second, delivery = client.get(f"/documents/{document}").json()["resolution"]
    assert first == {"sku": mint(CATALOG[0]), "score": 2 / (RRF_K + 1)}
    assert second == {"sku": mint(CATALOG[1]), "score": 2 / (RRF_K + 1)}
    assert delivery == {"sku": None, "score": 1 / (RRF_K + 1) + 1 / (RRF_K + 3)}


def test_resolution_never_routes_a_line_with_no_entry(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
) -> None:
    """A resolver that finds no entry for any line leaves the clean case
    approved: resolution annotates and is no routing reason (#151)."""
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )

    def nowhere(_: str) -> Resolved:
        return Resolved(sku=None, score=0.0)

    settle(connection, replayed, nowhere)

    view = client.get(f"/documents/{document}").json()
    assert (view["status"], view["routing_reasons"]) == ("approved", [])
    assert view["resolution"] == [{"sku": None, "score": 0.0}] * 3


def test_the_match_result_is_the_same_with_and_without_resolution(
    database_url: str,
    schema: str,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    """The same held case settled once through the catalog and once through
    a resolver that finds nothing, each on a fresh schema: the matcher never
    reads a SKU, so the match, the status and the reasons are the same."""
    invoice = pdf(tmp_path, "eval0005")
    case = case_text(ordered("eval0005", billed_above=True))

    def nowhere(_: str) -> Resolved:
        return Resolved(sku=None, score=None)

    views = []
    for resolve in (resolver, nowhere):
        with connect(database_url) as fresh:
            fresh.autocommit = True
            drop_schema(fresh, schema)
        prepare(database_url, schema)
        with (
            open_schema(database_url, schema) as opened,
            TestClient(api.create(database_url, schema)) as client,
        ):
            document = uploaded(client, invoice, case)
            settle(opened, replayed, resolve)
            view = client.get(f"/documents/{document}").json()
        views.append((view["status"], view["routing_reasons"], view["match"]))

    with_catalog, without = views
    assert with_catalog == without
    assert with_catalog[:2] == ("needs_review", ["match held"])


def test_an_injected_hold_goes_to_review_with_match_held(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    case = ordered("eval0005", billed_above=True)
    document = uploaded(client, pdf(tmp_path, "eval0005"), case_text(case))

    settle(connection, replayed, resolver)

    view = client.get(f"/documents/{document}").json()
    assert view["status"] == "needs_review"
    assert view["routing_reasons"] == ["match held"]
    (finding,) = view["match"]["findings"]
    assert finding["type"] == "price variance"
    assert finding["severity"] == "hold"
    assert finding["explanation"].startswith("price variance, PO line 0, amount")


def test_totals_that_disagree_go_to_review_with_gate_failed(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0003"), case_text(ordered("eval0003"))
    )

    settle(connection, replayed, resolver)

    view = client.get(f"/documents/{document}").json()
    assert view["status"] == "needs_review"
    assert view["routing_reasons"] == ["gate failed"]
    assert view["match"]["verdict"] == "approvable"


def test_a_document_with_no_reading_goes_to_review_from_received(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0006"), case_text(ordered("eval0005"))
    )

    assert settle(connection, replayed, resolver) == ["needs_review"]
    view = client.get(f"/documents/{document}").json()
    assert view["routing_reasons"] == ["extraction failed"]
    assert view["reading"] is None
    assert view["resolution"] is None
    moves = [
        (each["from"], each["to"])
        for each in client.get(f"/documents/{document}/trace").json()["transitions"]
    ]
    assert moves == [(None, "received"), ("received", "needs_review")]


def test_a_pdf_the_saved_run_does_not_pin_fails_extraction(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    unknown = write_pdf(tmp_path / "unknown.pdf", width=500)
    document = uploaded(client, unknown, case_text(ordered("eval0005")))

    settle(connection, replayed, resolver)

    view = client.get(f"/documents/{document}").json()
    assert view["routing_reasons"] == ["extraction failed"]
    assert view["cost"] == "0"


def test_a_second_transition_from_the_same_status_is_refused(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    claim_and_advance(connection, replayed, resolver, wait=_no_wait)
    taken = claim(connection)
    assert taken is not None and taken.status == "extracted"

    advance(connection, taken, replayed, resolver)
    with pytest.raises(Refused):
        advance(connection, taken, replayed, resolver)

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
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    uploaded(client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005")))
    settle(connection, replayed, resolver)

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        connection.execute("UPDATE transitions SET actor = 'reviewer'")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        connection.execute("DELETE FROM transitions")


def test_a_repeated_upload_returns_the_same_document_read_once(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    invoice, case = pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    document = uploaded(client, invoice, case)
    settle(connection, replayed, resolver)
    reading = client.get(f"/documents/{document}").json()["reading"]

    respaced = json.dumps(json.loads(case), indent=2)
    again = uploaded(client, invoice, respaced, expected=200)
    settle(connection, replayed, resolver)

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
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, document_id), case_text(ordered("eval0005"))
    )
    settle(connection, replayed, resolver)

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
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    settle(connection, replayed, resolver)

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


MILLISECOND = timedelta(milliseconds=1)


def lapse(connection: Connection) -> Claim:
    """A test worker that takes a millisecond lease and never commits: the
    claim it took, once the lease has lapsed."""
    taken = claim(connection, MILLISECOND)
    assert taken is not None
    time.sleep(0.01)
    return taken


def moves(client: TestClient, document: int) -> list[tuple[str | None, str]]:
    """A document's transitions, from and to."""
    transitions = client.get(f"/documents/{document}/trace").json()["transitions"]
    return [(each["from"], each["to"]) for each in transitions]


def test_the_third_lapse_at_one_status_routes_as_pipeline_failed(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    claim_and_advance(connection, replayed, resolver, wait=_no_wait)
    for _ in range(LAPSES):
        lapse(connection)

    assert (
        claim_and_advance(connection, replayed, resolver, wait=_no_wait)
        == "needs_review"
    )
    view = client.get(f"/documents/{document}").json()
    assert view["routing_reasons"] == ["pipeline failed at extracted"]
    assert moves(client, document)[-1] == ("extracted", "needs_review")
    assert claim(connection) is None


def test_two_lapses_leave_the_document_to_move_on(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    for _ in range(LAPSES - 1):
        lapse(connection)

    settle(connection, replayed, resolver)

    assert client.get(f"/documents/{document}").json()["status"] == "approved"


def test_lapses_are_counted_per_status(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    for status in ("received", "extracted", "validated"):
        for _ in range(LAPSES - 1):
            assert lapse(connection).status == status
        assert claim_and_advance(connection, replayed, resolver, wait=_no_wait) != (
            "needs_review"
        )

    settle(connection, replayed, resolver)

    assert client.get(f"/documents/{document}").json()["status"] == "approved"


def test_a_pipeline_failure_carries_the_reasons_known_where_it_stands(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0003"), case_text(ordered("eval0003"))
    )
    for _ in range(3):
        claim_and_advance(connection, replayed, resolver, wait=_no_wait)
    for _ in range(LAPSES):
        assert lapse(connection).status == "resolved"

    claim_and_advance(connection, replayed, resolver, wait=_no_wait)

    view = client.get(f"/documents/{document}").json()
    assert view["status"] == "needs_review"
    assert view["routing_reasons"] == ["gate failed", "pipeline failed at resolved"]
    assert view["match"] is None


def test_a_lapsed_lease_is_retaken_from_the_last_checkpoint_and_written_once(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    claim_and_advance(connection, replayed, resolver, wait=_no_wait)
    stale = lapse(connection)

    retaken = claim(connection)
    assert retaken is not None
    assert (retaken.document, retaken.status) == (document, "extracted")
    assert advance(connection, retaken, replayed, resolver) == "validated"
    with pytest.raises(Refused):
        advance(connection, stale, replayed, resolver)

    settle(connection, replayed, resolver)
    assert moves(client, document) == [
        (None, "received"),
        ("received", "extracted"),
        ("extracted", "validated"),
        ("validated", "resolved"),
        ("resolved", "matched"),
        ("matched", "approved"),
    ]
    assert len(client.get(f"/documents/{document}/trace").json()["vendor_calls"]) == 1


def test_a_vendor_call_under_a_lapsed_lease_still_counts(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    stale = lapse(connection)
    assert (
        claim_and_advance(connection, replayed, resolver, wait=_no_wait) == "extracted"
    )

    with pytest.raises(Refused):
        advance(connection, stale, replayed, resolver, wait=_no_wait)

    calls = client.get(f"/documents/{document}/trace").json()["vendor_calls"]
    assert len(calls) == 2
    cost = Decimal(client.get(f"/documents/{document}").json()["cost"])
    assert cost == 2 * Decimal(str(SAVED["eval0005"]["cost"]))
    assert moves(client, document)[:2] == [
        (None, "received"),
        ("received", "extracted"),
    ]


def test_a_document_stuck_at_received_carries_pipeline_failed_alone(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    for _ in range(LAPSES):
        lapse(connection)

    assert (
        claim_and_advance(connection, replayed, resolver, wait=_no_wait)
        == "needs_review"
    )
    view = client.get(f"/documents/{document}").json()
    assert view["routing_reasons"] == ["pipeline failed at received"]
    assert view["reading"] is None
    assert client.get(f"/documents/{document}/trace").json()["vendor_calls"] == []


def test_a_worker_that_raises_lets_its_lease_go_at_once(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolver: Resolve,
) -> None:
    """A document that fails the same way every time routes within the run,
    not after three full leases."""
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )
    claim_and_advance(connection, replayed, resolver, wait=_no_wait)

    def broken(_: object) -> None:
        raise RuntimeError("the gate broke")

    monkeypatch.setattr(loop, "gate", broken)
    for _ in range(LAPSES):
        with pytest.raises(RuntimeError, match="the gate broke"):
            claim_and_advance(connection, replayed, resolver, wait=_no_wait)

    assert (
        claim_and_advance(connection, replayed, resolver, wait=_no_wait)
        == "needs_review"
    )
    view = client.get(f"/documents/{document}").json()
    assert view["routing_reasons"] == ["pipeline failed at extracted"]
