"""The CLI on the synthetic fixture: `docmatch loop --backend replay` against a
real Postgres, then `docmatch pipeline --run` on what it saved.

Skipped without a database. It pins what is deterministic by exact equality,
each case's final status, routing reasons and cost per document, and of
latency only that a p50 and a p95 exist per status (#165).
"""

import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from docmatch.cli import main
from docmatch.pipeline.conftest import SYNTHETIC
from docmatch.pipeline.loop import PENDING
from docmatch.pipeline.saved import read_loop_run
from docmatch.pipeline.store import drop_schema
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
    database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Path]:
    """The fixture's loop run on replay, run once, its schema dropped after."""
    out = tmp_path_factory.mktemp("loop")
    code = main(
        [
            "loop",
            "--backend",
            "replay",
            "--run",
            str(LOOP_RUN),
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
    assert code == 0
    try:
        yield out
    finally:
        with connect(database_url) as connection:
            connection.autocommit = True
            drop_schema(connection, read_loop_run(out).database_schema)


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


def test_the_schema_is_kept_after_the_run(looped: Path, database_url: str) -> None:
    schema = read_loop_run(looped).database_schema

    with connect(database_url) as connection:
        found = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
            (schema,),
        ).fetchone()
    assert found == (3,)


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


def test_the_loop_run_carries_no_document_contents(looped: Path) -> None:
    """Rule 6: ids, statuses, numbers and times; no word any page carries."""
    saved = (looped / "loop.json").read_text("utf-8")

    for label in ("Lakeside Tools", "Hex key set", "INV-1003", "412.50"):
        assert label not in saved
