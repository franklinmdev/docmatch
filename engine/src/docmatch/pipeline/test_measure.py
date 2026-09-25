"""The CLI on the synthetic fixture: `docmatch loop` on replay and on the
labels against a real Postgres, then `docmatch pipeline --run` on what it
saved.

Skipped without a database. It pins what is deterministic by exact equality,
each case's final status, routing reasons and cost per document, and of
latency only that a p50 and a p95 exist per status (#165).
"""

import re
import subprocess
import sys
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from docmatch.cli import main
from docmatch.docile.dataset import DocileDataset
from docmatch.matching.generator import Seed, one_per_document
from docmatch.matching.records import Record
from docmatch.metrics.fields import labeled_fields
from docmatch.metrics.line_items import labeled_line_items
from docmatch.pipeline import faked, labels, measure
from docmatch.pipeline.conftest import SYNTHETIC
from docmatch.pipeline.loop import PENDING, Reading
from docmatch.pipeline.measure import LoopError, _Server
from docmatch.pipeline.report import report
from docmatch.pipeline.saved import read_loop_run
from docmatch.pipeline.serve import catalog_schema
from docmatch.pipeline.store import drop_schema, open_schema
from docmatch.resolution.catalog import ResolutionError
from docmatch.resolution.store import connect, resolve_database_url

LOOP_RUN = SYNTHETIC / "runs" / "loop"


@pytest.fixture(scope="module")
def database_url() -> str:
    """The `database_url` fixture's own precedence and skip, once per module,
    since the loop below runs once for every test here."""
    url = resolve_database_url(None)
    try:
        connect(url).close()
    except ResolutionError as error:
        pytest.skip(str(error))
    return url


@pytest.fixture(scope="module")
def looped(
    database_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    """The fixture's loop run on replay, run once, its schemas dropped after.
    The server is run from `faked`, so it embeds the catalog with the fake
    and no weights are loaded; CI's Pipeline step runs the real one."""
    yield from _looped(
        tmp_path_factory.mktemp("loop"),
        database_url,
        "--backend",
        "replay",
        "--run",
        str(LOOP_RUN),
    )


@pytest.fixture(scope="module")
def looped_on_labels(
    database_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    """The labels control's loop run over the same fixture, run once."""
    yield from _looped(
        tmp_path_factory.mktemp("labels"), database_url, "--backend", labels.BACKEND
    )


def _looped(out: Path, database_url: str, *backend: str) -> Iterator[Path]:
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(measure, "COMMAND", faked.__name__)
        code = looping(out, database_url, *backend)
    assert code == 0
    try:
        yield out
    finally:
        schema = read_loop_run(out).database_schema
        with connect(database_url) as connection:
            connection.autocommit = True
            drop_schema(connection, schema)
            drop_schema(connection, catalog_schema(schema))


def looping(out: Path, database_url: str, *backend: str) -> int:
    return main(
        [
            "loop",
            *backend,
            "--data-dir",
            str(SYNTHETIC),
            "--manifest",
            str(LOOP_RUN / "manifest.json"),
            "--copies",
            str(SYNTHETIC / "copies"),
            "--database-url",
            database_url,
            "--out",
            str(out),
        ]
    )


def test_the_fixture_settles_each_case_as_pinned(looped: Path) -> None:
    run = read_loop_run(looped)

    settled = {
        each.document_id: (
            each.status,
            each.routing_reasons,
            sum((call.cost for call in each.vendor_calls), Decimal(0)),
        )
        for each in run.cases
    }
    assert settled == {
        "eval0004": ("needs_review", ("extraction failed",), Decimal("0.00094")),
        "eval0006": ("needs_review", ("match held",), Decimal("0.00048")),
        "eval0003": ("needs_review", ("gate failed",), Decimal("0.00132")),
        "eval0005": ("approved", (), Decimal("0.00061")),
    }
    assert run.without_lines == ("eval0002",)
    assert (run.backend, run.source) == ("replay", str(LOOP_RUN))


def test_the_labels_run_settles_every_case_with_its_labels_as_the_reading(
    looped_on_labels: Path, database_url: str
) -> None:
    run = read_loop_run(looped_on_labels)
    dataset = DocileDataset(SYNTHETIC)

    assert (run.backend, run.source, run.requested_model) == (
        labels.BACKEND,
        None,
        labels.MODEL,
    )
    assert {each.document_id for each in run.cases} == {
        "eval0003",
        "eval0004",
        "eval0005",
        "eval0006",
    }
    with open_schema(database_url, run.database_schema) as connection:
        for each in run.cases:
            assert (each.vendor_calls, each.misread) == ((), ())
            found = connection.execute(
                "SELECT reading FROM documents WHERE id = %s", (each.id,)
            ).fetchone()
            assert found is not None
            reading = Reading.model_validate(found[0]).prediction
            annotation = dataset.annotation(each.document_id)
            assert reading.header == labeled_fields(annotation)
            assert reading.rows == labeled_line_items(annotation)
    assert report(run).cost_per_document == Decimal(0)


def test_the_labels_run_settles_each_case_and_its_ladder_as_pinned(
    looped_on_labels: Path,
) -> None:
    """eval0003's labeled totals disagree (412.50 against 400.00 due), so the
    gate fails on the labels too; eval0004's missing line is a note, so it
    approves and never escapes; eval0006's short-ship holds from P4 up. No
    P5, as labels carry no confidence."""
    run = read_loop_run(looped_on_labels)

    assert {
        each.document_id: (each.status, each.routing_reasons) for each in run.cases
    } == {
        "eval0004": ("approved", ()),
        "eval0006": ("needs_review", ("match held",)),
        "eval0003": ("needs_review", ("gate failed",)),
        "eval0005": ("approved", ()),
    }
    assert [
        (each.policy.name, each.reviewed, each.approved, each.escaped)
        for each in report(run).ladder
    ] == [
        ("P0", 0, 4, 1),
        ("P1", 0, 4, 1),
        ("P2", 1, 3, 1),
        ("P3", 1, 3, 1),
        ("P4", 2, 2, 0),
    ]


def test_the_loop_run_keeps_each_cases_truth(looped: Path) -> None:
    truth = {
        each.document_id: [one.type for one in each.truth]
        for each in read_loop_run(looped).cases
    }

    assert truth == {
        "eval0004": ["missing line"],
        "eval0006": ["short-ship"],
        "eval0003": [],
        "eval0005": [],
    }


def test_the_loop_run_keeps_what_the_ladder_reads(looped: Path) -> None:
    """eval0005's issue date is read unlike its label, with the gate still
    passing, and its due date at 0.35 confidence (#173)."""
    kept = {
        each.document_id: (each.gate, each.holds, each.gated_confidence, each.misread)
        for each in read_loop_run(looped).cases
    }

    assert kept == {
        "eval0004": (None, (), (), ()),
        "eval0006": ("not checked", ("short-ship",), (), ()),
        "eval0003": ("failed", (), (None, 0.72), ()),
        "eval0005": ("passed", (), (0.35, 0.99, 0.97, 0.99), ("date_issue",)),
    }


def test_the_ladder_on_the_fixture_is_pinned(looped: Path) -> None:
    """Review count, approved, and escapes in all and by cause, per rung; P5
    because the fixture's replay source saved a confidence."""
    run = read_loop_run(looped)
    rungs = report(run).ladder

    assert [
        (
            each.policy.name,
            each.policy.edge,
            each.reviewed,
            each.approved,
            each.escaped,
            each.injected_hold,
            each.misread,
        )
        for each in rungs
    ] == [
        ("P0", None, 0, 4, 2, 1, 1),
        ("P1", None, 1, 3, 2, 1, 1),
        ("P2", None, 2, 2, 2, 1, 1),
        ("P3", None, 2, 2, 2, 1, 1),
        ("P4", None, 3, 1, 1, 0, 1),
        *(("P5", edge / 10, 3, 1, 1, 0, 1) for edge in range(1, 4)),
        *(("P5", edge / 10, 4, 0, 0, 0, 0) for edge in range(4, 10)),
    ]
    p4 = next(each for each in rungs if each.policy.name == "P4")
    assert p4.approved == sum(each.status == "approved" for each in run.cases)


def test_the_schema_is_kept_after_the_run(looped: Path, database_url: str) -> None:
    schema = read_loop_run(looped).database_schema

    with connect(database_url) as connection:
        found = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
            (schema,),
        ).fetchone()
    assert found == (4,)


def test_pipeline_prints_p50_and_p95_per_status_from_the_file_alone(
    looped: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["pipeline", "--run", str(looped)]) == 0

    printed = capsys.readouterr().out
    assert f"Loop run, replay of {LOOP_RUN}" in printed
    assert "4 of 5, 1 with no labeled lines seed no case" in printed
    assert re.search(r"end to end, p50\s+\d+\.\d{3} s", printed)
    assert re.search(r"end to end, p95\s+\d+\.\d{3} s", printed)
    assert re.search(r"cost per document\s+\$0\.000838", printed)
    for status in PENDING:
        row = next(
            line for line in printed.splitlines() if line.split()[:1] == [status]
        )
        assert re.fullmatch(
            rf"\s*{status}(\s+\d+\.\d{{3}} s){{4}}\s+\$\d\.\d{{6}}", row
        ), row
    received = next(line for line in printed.splitlines() if "received" in line)
    assert received.endswith("$0.000838")
    assert re.search(
        r"\n  P4 \+ match held on any hold type.* 0\.750 +1 +1\.000", printed
    )
    assert "confidence below 0.9" in printed
    assert "no labels run given" in printed


def test_the_loop_run_carries_no_document_contents(looped: Path) -> None:
    """Rule 6: ids, statuses, numbers and times; no word any page carries."""
    saved = (looped / "loop.json").read_text("utf-8")

    for label in ("Lakeside Tools", "Hex key set", "INV-1003", "412.50"):
        assert label not in saved


def test_a_server_that_drops_the_connection_is_a_loop_error(tmp_path: Path) -> None:
    """A request the server never answers, dead or past the timeout, is
    reported with the log to read, never raised as the client's own error."""

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    stopped = subprocess.Popen([sys.executable, "-c", "pass"])
    stopped.wait()
    log = tmp_path / "serve.log"
    server = _Server(
        stopped,
        httpx.Client(
            base_url="http://127.0.0.1:1", transport=httpx.MockTransport(refused)
        ),
        log,
    )
    case = one_per_document(
        (Seed("eval0005", Record(header={}, lines=({"line_item_quantity": ("1",)},))),)
    )[0]

    with pytest.raises(LoopError, match=re.escape(str(log))):
        server.run(case, SYNTHETIC / "copies", {})
