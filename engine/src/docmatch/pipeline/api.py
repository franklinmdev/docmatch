"""The HTTP API over one loop run's schema.

FastAPI 0.141.1 with python-multipart 0.0.32 for the upload, both read on
PyPI and in FastAPI's docs 2026-09-24 (tutorial/request-forms-and-files,
tutorial/testing, advanced/events): a file part declared as `bytes` with
`File()` beside a text part with `Form()`, a sync endpoint run in the thread
pool, and `TestClient` from `fastapi.testclient` over httpx 0.28.1.

`serve` binds it to 127.0.0.1 with no auth and no versioning (rule 1). The
backend is fixed when the server starts, so the upload carries no benchmark
parameter (#164). Each request opens its own connection on the schema, which
`serve` has already prepared.

- `POST /documents`: part `invoice`, the PDF unchanged, and part `case`, the
  case as JSON text. 201 with the new document, 200 with the one already
  stored under the same content, 422 for a PDF pdfium cannot open or a case
  that is not one.
- `GET /documents/{id}`: the document's full view.
- `GET /documents/{id}/trace`: its transitions and vendor calls, without
  what came back.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, status

from docmatch.extraction.pages import PageError
from docmatch.pipeline import loop
from docmatch.pipeline.case import CaseError, read_case
from docmatch.pipeline.store import Connection, open_schema


def create(database_url: str, schema: str) -> FastAPI:
    """The API over one prepared schema."""
    app = FastAPI(title="docmatch")

    def connection() -> Iterator[Connection]:
        opened = open_schema(database_url, schema)
        try:
            yield opened
        finally:
            opened.close()

    Connected = Annotated[Connection, Depends(connection)]

    @app.post("/documents", status_code=status.HTTP_201_CREATED)
    def upload(
        invoice: Annotated[bytes, File()],
        case: Annotated[str, Form()],
        response: Response,
        connection: Connected,
    ) -> dict[str, object]:
        try:
            stored = loop.upload(connection, invoice, read_case(case))
        except (CaseError, PageError) as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
            ) from None
        if not stored.created:
            response.status_code = status.HTTP_200_OK
        return _found(loop.view(connection, stored.document))

    @app.get("/documents/{document}")
    def document(document: int, connection: Connected) -> dict[str, object]:
        return _found(loop.view(connection, document))

    @app.get("/documents/{document}/trace")
    def trace(document: int, connection: Connected) -> dict[str, object]:
        return _found(loop.trace(connection, document))

    return app


def _found(body: dict[str, object] | None) -> dict[str, object]:
    if body is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such document")
    return body
