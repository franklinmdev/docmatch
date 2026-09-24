"""A loop run as `docmatch loop` saves it and `docmatch pipeline` reads it.

One file, `loop.json`, in the directory `loop --out` names, under the ignored
`data/runs/` for a real run. It holds what the report needs and nothing a
document says (rule 6): per case its status, routing reasons, the truth the
generator injected (type, place and band), every transition with both its
times, every vendor call without what came back, and the confidence numbers
the backend returned, keyed by fieldtype, where it returns any. The report
reads this file and nothing else, never Postgres, and calls no model (#152).
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from docmatch.extraction.extractor import Confidence
from docmatch.matching.generator import Injected
from docmatch.metrics.fields import PredictionError, first_problem
from docmatch.pipeline.loop import Status

LOOP_FILE = "loop.json"


class SavedTransition(BaseModel):
    """One status change, as the trace gives it."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_status: Status | None = Field(alias="from")
    to: Status
    taken_at: datetime | None
    """When the loop took the document up; None for the upload, which has no
    wait before it."""
    committed_at: datetime
    actor: str
    reasons: tuple[str, ...] = ()


class SavedCall(BaseModel):
    """One request to a vendor, without what it returned."""

    model_config = ConfigDict(frozen=True)

    status: Status
    attempt: int
    units: dict[str, int]
    cost: Decimal
    served_model: str | None
    started_at: datetime
    ended_at: datetime
    failed: bool


class SavedCase(BaseModel):
    """One document's case through the loop."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    id: int
    """The document's id in the loop run's schema, for a reviewer to find it."""
    status: Status
    routing_reasons: tuple[str, ...]
    truth: tuple[Injected, ...]
    """What the generator injected: none for a clean case."""
    transitions: tuple[SavedTransition, ...]
    vendor_calls: tuple[SavedCall, ...]
    confidence: Confidence | None = None


class LoopRun(BaseModel):
    """One measurement: one backend, one case per document with lines."""

    model_config = ConfigDict(frozen=True)

    backend: str
    source: str | None = None
    """The saved run replay answered from; None for a live backend."""
    requested_model: str
    database_schema: str
    """The Postgres schema the loop ran on, kept for review."""
    seed: int
    manifest: str
    documents: int
    """Every document the manifest pins, with lines or not."""
    without_lines: tuple[str, ...]
    """The pinned documents with no labeled lines, which seed no case (#152)."""
    cases: tuple[SavedCase, ...]


def write_loop_run(run: LoopRun, directory: Path) -> Path:
    """The run's file in `directory`, made when missing; its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOOP_FILE
    path.write_text(run.model_dump_json(indent=2, by_alias=True) + "\n", "utf-8")
    return path


def read_loop_run(directory: Path) -> LoopRun:
    """The loop run saved in `directory`, or a message saying what is wrong."""
    path = directory / LOOP_FILE
    try:
        body = path.read_bytes()
    except OSError as error:
        raise PredictionError(
            f"cannot read the loop run {path}: {error}; `docmatch loop --out` writes it"
        ) from error
    try:
        return LoopRun.model_validate_json(body)
    except ValidationError as error:
        raise PredictionError(
            f"{path} is not a loop run `docmatch loop` wrote. {first_problem(error)}"
        ) from error
