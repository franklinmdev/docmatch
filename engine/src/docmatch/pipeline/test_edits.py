"""Tests for a reviewer's edits: each one saved, rerunning the gate,
resolution and match in the request, and the net corrections they leave at
the decision, through the HTTP API against a real Postgres, skipped when none
answers, with replay answering from a saved run."""

from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from docmatch.pipeline.conftest import (
    CATALOG,
    case_text,
    ordered,
    pdf,
    settle,
    uploaded,
)
from docmatch.pipeline.loop import Resolve
from docmatch.pipeline.replay import Replay
from docmatch.pipeline.store import Connection
from docmatch.resolution.catalog import mint


def in_review(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
    document_id: str,
    *,
    billed_above: bool = False,
) -> int:
    """One document uploaded and settled by the loop."""
    document = uploaded(
        client,
        pdf(tmp_path, document_id),
        case_text(
            ordered(
                "eval0005" if document_id == "eval0006" else document_id,
                billed_above=billed_above,
            )
        ),
    )
    settle(connection, replayed, resolver)
    return document


def edit(client: TestClient, document: int, body: dict[str, object]) -> Any:
    """The full view an edit answers with."""
    response = client.post(f"/documents/{document}/edits", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_an_edit_that_fixes_a_misread_total_clears_the_failed_gate(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    """eval0003 reads a gross of $412.50 and $400.00 due; putting the due
    amount right passes the gate, and the document stays in review with the
    reason it was routed for."""
    document = in_review(client, connection, replayed, tmp_path, resolver, "eval0003")

    view = edit(
        client,
        document,
        {"kind": "header", "fieldtype": "amount_due", "value": "$412.50"},
    )

    assert view["status"] == "needs_review"
    assert view["gate"]["verdict"] == "passed"
    assert view["routing_reasons"] == ["gate failed"]
    assert view["reading"]["prediction"]["fields"]["amount_due"] == "$412.50"
    assert view["corrections"] == [
        {
            "kind": "header",
            "fieldtype": "amount_due",
            "read": ["$400.00"],
            "left": ["$412.50"],
        }
    ]
    assert client.get(f"/documents/{document}").json() == view


def test_an_edit_to_a_line_cell_reruns_the_match(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    """The order prices the first line a fifth below the 900.00 billed;
    billing it at the order's 720.00 leaves nothing to hold."""
    document = in_review(
        client, connection, replayed, tmp_path, resolver, "eval0005", billed_above=True
    )

    view = edit(
        client,
        document,
        {
            "kind": "cell",
            "line": 0,
            "fieldtype": "line_item_amount_gross",
            "value": "720.00",
        },
    )

    assert view["match"]["verdict"] == "approvable"
    assert view["match"]["findings"] == []
    assert view["routing_reasons"] == ["match held"]
    assert view["corrections"] == [
        {
            "kind": "cell",
            "line": 0,
            "fieldtype": "line_item_amount_gross",
            "read": ["900.00"],
            "left": ["720.00"],
        }
    ]


def test_an_edit_to_a_description_reruns_resolution(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(
        client, connection, replayed, tmp_path, resolver, "eval0005", billed_above=True
    )

    view = edit(
        client,
        document,
        {
            "kind": "cell",
            "line": 2,
            "fieldtype": "line_item_description",
            "value": "Work gloves",
        },
    )

    assert view["resolution"][2]["sku"] == mint(CATALOG[2])


def test_a_line_removed_leaves_the_reading_and_restored_comes_back(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(
        client, connection, replayed, tmp_path, resolver, "eval0005", billed_above=True
    )
    before = client.get(f"/documents/{document}").json()

    removed = edit(client, document, {"kind": "line removed", "line": 1})

    lines = removed["reading"]["prediction"]["line_items"]
    assert [each["line_item_description"] for each in lines] == [
        "Cable reel, 25 m",
        "Delivery",
    ]
    assert removed["line_ids"] == [0, 2]
    assert len(removed["resolution"]) == 2
    assert removed["corrections"] == [
        {
            "kind": "line removed",
            "line": 1,
            "read": {
                "line_item_quantity": ["4"],
                "line_item_description": ["Junction box"],
                "line_item_amount_gross": ["42.00"],
            },
        }
    ]

    restored = edit(client, document, {"kind": "line restored", "line": 1})

    for key in ("reading", "gate", "resolution", "match"):
        assert restored[key] == before[key]
    assert restored["line_ids"] == [0, 1, 2]
    assert restored["corrections"] == []


def test_a_line_added_is_a_correction_once_it_carries_a_cell(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(
        client, connection, replayed, tmp_path, resolver, "eval0005", billed_above=True
    )

    added = edit(client, document, {"kind": "line added"})

    assert added["line_ids"] == [0, 1, 2, 3]
    assert added["reading"]["prediction"]["line_items"][3] == {}
    assert added["corrections"] == []

    filled = edit(
        client,
        document,
        {
            "kind": "cell",
            "line": 3,
            "fieldtype": "line_item_description",
            "value": "Junction box",
        },
    )

    assert filled["resolution"][3]["sku"] == mint(CATALOG[1])
    assert filled["corrections"] == [
        {
            "kind": "line added",
            "line": 3,
            "left": {"line_item_description": ["Junction box"]},
        }
    ]


def test_an_edit_outside_review_is_refused(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    approved = in_review(client, connection, replayed, tmp_path, resolver, "eval0005")
    pending = uploaded(
        client, pdf(tmp_path, "eval0003"), case_text(ordered("eval0003"))
    )
    change = {"kind": "header", "fieldtype": "amount_due", "value": "1.00"}

    for document in (approved, pending):
        response = client.post(f"/documents/{document}/edits", json=change)
        assert response.status_code == 409
    assert client.post("/documents/999/edits", json=change).status_code == 404
    assert client.get(f"/documents/{approved}").json()["corrections"] == []


def test_an_edit_after_the_decision_is_refused(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(client, connection, replayed, tmp_path, resolver, "eval0003")
    client.post(f"/documents/{document}/decision", json={"decision": "rejected"})

    response = client.post(
        f"/documents/{document}/edits",
        json={"kind": "header", "fieldtype": "amount_due", "value": "$412.50"},
    )

    assert response.status_code == 409
    assert client.get(f"/documents/{document}").json()["gate"]["verdict"] == "failed"


def test_a_failed_extraction_takes_no_edit(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(client, connection, replayed, tmp_path, resolver, "eval0006")

    response = client.post(f"/documents/{document}/edits", json={"kind": "line added"})

    assert response.status_code == 409
    view = client.get(f"/documents/{document}").json()
    assert (view["reading"], view["corrections"]) == (None, [])


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "purchase order", "line": 0, "value": "1"},
        {"kind": "receipt", "line": 0, "value": "1"},
        {"kind": "header", "fieldtype": "line_item_quantity", "value": "1"},
        {"kind": "cell", "line": 0, "fieldtype": "amount_due", "value": "1"},
        {"kind": "cell", "line": 3, "fieldtype": "line_item_quantity", "value": "1"},
        {"kind": "line removed", "line": 7},
        {"kind": "line restored", "line": 0},
    ],
)
def test_an_edit_the_reading_cannot_take_is_refused(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
    change: dict[str, object],
) -> None:
    """The order and the receipt are never editable, and an edit names a
    header fieldtype, a line cell, or a line the reading has."""
    document = in_review(client, connection, replayed, tmp_path, resolver, "eval0003")

    response = client.post(f"/documents/{document}/edits", json=change)

    assert response.status_code == 422
    assert client.get(f"/documents/{document}").json()["corrections"] == []


def test_a_cell_on_a_removed_line_is_refused(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(client, connection, replayed, tmp_path, resolver, "eval0003")
    edit(client, document, {"kind": "line removed", "line": 0})

    for change in (
        {"kind": "cell", "line": 0, "fieldtype": "line_item_quantity", "value": "1"},
        {"kind": "line removed", "line": 0},
    ):
        assert (
            client.post(f"/documents/{document}/edits", json=change).status_code == 422
        )


def test_a_value_put_back_is_no_correction_at_the_decision(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(client, connection, replayed, tmp_path, resolver, "eval0003")
    edit(
        client,
        document,
        {"kind": "header", "fieldtype": "amount_due", "value": "$412.50"},
    )
    edit(
        client,
        document,
        {"kind": "header", "fieldtype": "amount_due", "value": "$400.00"},
    )

    decided = client.post(
        f"/documents/{document}/decision", json={"decision": "approved"}
    ).json()

    assert decided["status"] == "approved"
    assert decided["corrections"] == []


def test_an_overwritten_value_never_stands_at_the_decision(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(client, connection, replayed, tmp_path, resolver, "eval0003")
    for value in ("$41.25", "$412.50"):
        edit(
            client,
            document,
            {"kind": "header", "fieldtype": "amount_due", "value": value},
        )
    edit(
        client,
        document,
        {"kind": "cell", "line": 2, "fieldtype": "line_item_quantity", "value": "2"},
    )
    edit(
        client,
        document,
        {"kind": "cell", "line": 2, "fieldtype": "line_item_quantity", "value": "1"},
    )

    decided = client.post(
        f"/documents/{document}/decision", json={"decision": "approved"}
    ).json()

    assert decided["corrections"] == [
        {
            "kind": "header",
            "fieldtype": "amount_due",
            "read": ["$400.00"],
            "left": ["$412.50"],
        },
        {
            "kind": "cell",
            "line": 2,
            "fieldtype": "line_item_quantity",
            "read": [],
            "left": ["1"],
        },
    ]


def test_a_rejection_keeps_its_corrections(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    """A reading put right on an invoice that is still held: the verdict and
    the reading are independent, so the correction stands."""
    document = in_review(
        client, connection, replayed, tmp_path, resolver, "eval0005", billed_above=True
    )
    edit(
        client,
        document,
        {"kind": "header", "fieldtype": "vendor_name", "value": "Beacon Electric"},
    )
    edit(client, document, {"kind": "line removed", "line": 2})

    decided = client.post(
        f"/documents/{document}/decision", json={"decision": "rejected"}
    ).json()

    assert decided["status"] == "rejected"
    assert [each["kind"] for each in decided["corrections"]] == [
        "header",
        "line removed",
    ]


def test_a_value_left_blank_is_no_longer_read(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(client, connection, replayed, tmp_path, resolver, "eval0003")

    view = edit(
        client, document, {"kind": "header", "fieldtype": "amount_due", "value": " "}
    )

    assert "amount_due" not in view["reading"]["prediction"]["fields"]
    assert view["gate"]["verdict"] == "not checked"
    assert view["corrections"] == [
        {"kind": "header", "fieldtype": "amount_due", "read": ["$400.00"], "left": []}
    ]


def test_edits_are_append_only(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    document = in_review(client, connection, replayed, tmp_path, resolver, "eval0003")
    edit(client, document, {"kind": "line added"})

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        connection.execute("UPDATE edits SET made_at = now()")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        connection.execute("DELETE FROM edits")
