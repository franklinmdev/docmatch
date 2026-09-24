"""Tests for what the review page calls: the queue, a page's image and the
decision, through the HTTP API against a real Postgres, skipped when none
answers, with replay answering from a saved run."""

from pathlib import Path

from fastapi.testclient import TestClient

from docmatch.pipeline.conftest import case_text, ordered, pdf
from docmatch.pipeline.loop import Resolve
from docmatch.pipeline.replay import Replay
from docmatch.pipeline.store import Connection
from docmatch.pipeline.test_loop import settle, uploaded

PNG = b"\x89PNG\r\n\x1a\n"


def routed(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> dict[str, int]:
    """One document approved by the system, one held, one with no reading."""
    documents = {
        "approved": uploaded(
            client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
        ),
        "held": uploaded(
            client,
            pdf(tmp_path, "eval0005"),
            case_text(ordered("eval0005", billed_above=True)),
        ),
        "failed": uploaded(
            client, pdf(tmp_path, "eval0006"), case_text(ordered("eval0005"))
        ),
    }
    settle(connection, replayed, resolver)
    return documents


def test_the_queue_lists_exactly_the_documents_in_review(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    documents = routed(client, connection, replayed, tmp_path, resolver)

    queue = client.get("/documents", params={"status": "needs_review"}).json()

    assert queue == {
        "documents": [
            {
                "id": documents["held"],
                "status": "needs_review",
                "routing_reasons": ["match held"],
            },
            {
                "id": documents["failed"],
                "status": "needs_review",
                "routing_reasons": ["extraction failed"],
            },
        ]
    }


def test_the_queue_refuses_a_status_that_is_not_one(client: TestClient) -> None:
    assert client.get("/documents", params={"status": "pending"}).status_code == 422
    assert client.get("/documents").status_code == 422


def test_the_view_names_the_backend_the_server_reads_with(
    client: TestClient, tmp_path: Path
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )

    assert client.get(f"/documents/{document}").json()["backend"] == "replay"


def test_each_page_is_a_png(client: TestClient, tmp_path: Path) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )

    page = client.get(f"/documents/{document}/pages/1")

    assert page.status_code == 200
    assert page.headers["content-type"] == "image/png"
    assert page.content.startswith(PNG)


def test_a_page_the_document_does_not_have_is_not_found(
    client: TestClient, tmp_path: Path
) -> None:
    document = uploaded(
        client, pdf(tmp_path, "eval0005"), case_text(ordered("eval0005"))
    )

    assert client.get(f"/documents/{document}/pages/0").status_code == 404
    assert client.get(f"/documents/{document}/pages/2").status_code == 404
    assert client.get("/documents/999/pages/1").status_code == 404


def test_a_reviewer_approves_a_document_in_review(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    held = routed(client, connection, replayed, tmp_path, resolver)["held"]

    response = client.post(f"/documents/{held}/decision", json={"decision": "approved"})

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    last = client.get(f"/documents/{held}/trace").json()["transitions"][-1]
    assert (last["from"], last["to"], last["actor"]) == (
        "needs_review",
        "approved",
        "reviewer",
    )


def test_a_reviewer_rejects_a_failed_extraction(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    failed = routed(client, connection, replayed, tmp_path, resolver)["failed"]

    response = client.post(
        f"/documents/{failed}/decision", json={"decision": "rejected"}
    )

    assert response.json()["status"] == "rejected"
    last = client.get(f"/documents/{failed}/trace").json()["transitions"][-1]
    assert (last["to"], last["actor"]) == ("rejected", "reviewer")


def test_a_second_decision_is_refused(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    held = routed(client, connection, replayed, tmp_path, resolver)["held"]
    client.post(f"/documents/{held}/decision", json={"decision": "rejected"})

    second = client.post(f"/documents/{held}/decision", json={"decision": "approved"})

    assert second.status_code == 409
    assert client.get(f"/documents/{held}").json()["status"] == "rejected"


def test_a_decision_outside_review_is_refused(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    documents = routed(client, connection, replayed, tmp_path, resolver)
    pending = uploaded(
        client, pdf(tmp_path, "eval0003"), case_text(ordered("eval0003"))
    )

    for document in (documents["approved"], pending):
        response = client.post(
            f"/documents/{document}/decision", json={"decision": "rejected"}
        )
        assert response.status_code == 409
    assert client.get(f"/documents/{pending}").json()["status"] == "received"


def test_a_decision_is_approved_or_rejected_and_nothing_else(
    client: TestClient,
    connection: Connection,
    replayed: Replay,
    tmp_path: Path,
    resolver: Resolve,
) -> None:
    held = routed(client, connection, replayed, tmp_path, resolver)["held"]

    response = client.post(
        f"/documents/{held}/decision", json={"decision": "needs_review"}
    )

    assert response.status_code == 422
    assert (
        client.post(
            "/documents/999/decision", json={"decision": "approved"}
        ).status_code
        == 404
    )
