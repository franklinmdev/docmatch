"""Tests for the docmatch command line.

Every case builds a dataset from the synthetic fixture in a temporary
directory, so the suite runs in CI with no dataset present.
"""

import json
import re
import shutil
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from functools import partial
from pathlib import Path

import pytest

from docmatch import cli
from docmatch.cli import main, render_extract, render_pipeline, render_resolve
from docmatch.conftest import TEST_SCHEMA
from docmatch.docile.dataset import DocileDataset
from docmatch.evals import public
from docmatch.evals.conftest import annotate
from docmatch.evals.manifest import Manifest, load, rank, select, write
from docmatch.evals.public import FetchError
from docmatch.evals.run import read_predictions
from docmatch.extraction import gemini
from docmatch.extraction.conftest import write_pdf
from docmatch.extraction.extractor import Confidence, Usage
from docmatch.extraction.run import DocumentRun, Run
from docmatch.extraction.test_run import READING, FakeExtractor, a_subset
from docmatch.metrics.fields import Prediction
from docmatch.metrics.line_items import labeled_line_items
from docmatch.pipeline import labels
from docmatch.pipeline.report import report
from docmatch.pipeline.store import drop_schema, prepare
from docmatch.pipeline.test_report import approved, failed_extraction
from docmatch.pipeline.test_report import run as loop_run
from docmatch.resolution import run as resolution
from docmatch.resolution.catalog import CatalogCounts
from docmatch.resolution.conftest import FAKE_VERSIONS, fake_loader
from docmatch.resolution.models import ModelVersions
from docmatch.resolution.queries import (
    BAND_TARGETS,
    KIND_SHARES,
    NOISE_KINDS,
    Query,
    QuerySet,
    Share,
    Slice,
    SliceName,
)
from docmatch.resolution.store import Build, Machine, ServerVersions, connect
from docmatch.resolution.sweep import Point, Sweep

EXPECTED_SHOW_OUTPUT = "\n".join(
    [
        "Document syn0001",
        "",
        "KILE fields (5)",
        "  vendor_name" + " " * 9 + "Synthetic Supplies Ltd",
        "  vendor_address" + " " * 6 + "12 Example Way",
        " " * 22 + "Testville, EX 00000",
        "  document_id" + " " * 9 + "SYN-0001",
        "  date_issue" + " " * 10 + "02/01/26",
        "  amount_total_gross" + " " * 2 + "236.00",
        "",
        # One column for the whole table, not one per line item: only item 2
        # carries a unit of measure, and it sets the width for both.
        "LIR line items (2)",
        "  line item 1",
        "    line_item_quantity" + " " * 10 + "2",
        "    line_item_description" + " " * 7 + "Blue widget",
        "    line_item_amount_gross" + " " * 6 + "100.00",
        "  line item 2",
        "    line_item_quantity" + " " * 10 + "1",
        "    line_item_units_of_measure" + " " * 2 + "EA",
        "    line_item_description" + " " * 7 + "Red widget",
        "    line_item_amount_gross" + " " * 6 + "136.00",
        "",
    ]
)


def test_show_prints_kile_fields_and_lir_line_items(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["show", "syn0001", "--data-dir", str(data_dir)])

    assert capsys.readouterr().out == EXPECTED_SHOW_OUTPUT
    assert exit_code == 0


def test_show_reports_an_unknown_document_without_a_traceback(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["show", "absent0001", "--data-dir", str(data_dir)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "absent0001" in captured.err


def test_show_points_at_the_readme_when_the_dataset_is_not_downloaded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["show", "syn0001", "--data-dir", str(tmp_path / "absent")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "README.md" in captured.err


def test_data_dir_falls_back_to_the_environment(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The dataset lives outside the repo on some machines."""
    monkeypatch.setenv("DOCMATCH_DATA_DIR", str(data_dir))

    exit_code = main(["show", "syn0001"])

    assert capsys.readouterr().out == EXPECTED_SHOW_OUTPUT
    assert exit_code == 0


def test_an_explicit_data_dir_beats_the_environment(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DOCMATCH_DATA_DIR", str(tmp_path / "absent"))

    exit_code = main(["show", "syn0001", "--data-dir", str(data_dir)])

    assert capsys.readouterr().out == EXPECTED_SHOW_OUTPUT
    assert exit_code == 0


EXPECTED_HEADER_ONLY_OUTPUT = "\n".join(
    [
        "Document syn0002",
        "",
        "KILE fields (3)",
        "  vendor_name" + " " * 9 + "Synthetic Supplies Ltd",
        "  document_id" + " " * 9 + "SYN-0002",
        "  amount_total_gross" + " " * 2 + "40.00",
        "",
        "LIR line items (0)",
        "",
    ]
)


def test_show_handles_a_document_with_no_line_items(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plenty of real invoices carry no line-item table at all."""
    exit_code = main(["show", "syn0002", "--data-dir", str(data_dir)])

    assert capsys.readouterr().out == EXPECTED_HEADER_ONLY_OUTPUT
    assert exit_code == 0


def prediction_file(directory: Path, body: str) -> Path:
    path = directory / "prediction.json"
    path.write_text(body, encoding="utf-8")
    return path


PREDICTION = """
{
  "fields": {
    "vendor_name": "Synthetic  Supplies Ltd",
    "document_id": "SYN-0002",
    "date_issue": "February 1, 2026",
    "amount_total_gross": "$236.00",
    "date_due": null
  },
  "line_items": [
    {
      "line_item_quantity": "1",
      "line_item_description": "Red widget",
      "line_item_amount_gross": "$136.00",
      "line_item_tax": "0.00"
    },
    {
      "line_item_quantity": "2",
      "line_item_description": "Blue widget",
      "line_item_amount_gross": "100"
    }
  ]
}
"""
"""One reformatted header hit, one wrong, one missing, and the two rows of the
table in the other order: the first exact, the second short a unit of measure
and carrying a tax cell the label does not."""

EXPECTED_SCORE_OUTPUT = "\n".join(
    [
        "Document syn0001",
        "",
        "Field score",
        "  precision  0.750",
        "  recall     0.600",
        "  F1         0.667",
        "  3 matched, 2 missing, 1 spurious",
        "",
        "Fields (5)",
        "  amount_total_gross  matched   236",
        "  date_issue          matched   2026-02-01",
        "  document_id         missing   syn-0001",
        "                      spurious  syn-0002",
        "  vendor_address      missing   12 example way testville, ex 00000",
        "  vendor_name         matched   synthetic supplies ltd",
        "",
        # Every cell of the reordered rows lines up, so only the row short a
        # unit of measure is wrong, and only that fieldtype is inaccurate.
        "Line-item score",
        "  precision  0.500",
        "  recall     0.500",
        "  F1         0.500",
        "  1 matched, 1 missing, 1 spurious",
        "",
        "Cell accuracy (5)",
        "  line_item_amount_gross" + " " * 6 + "1.000  2 of 2",
        "  line_item_description" + " " * 7 + "1.000  2 of 2",
        "  line_item_quantity" + " " * 10 + "1.000  2 of 2",
        "  line_item_tax" + " " * 15 + "       0 of 0, 1 spurious",
        "  line_item_units_of_measure  0.000  0 of 1",
        "",
    ]
)


def test_score_reports_both_numbers_and_what_went_into_them(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = prediction_file(tmp_path, PREDICTION)

    exit_code = main(
        ["score", "syn0001", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    assert capsys.readouterr().out == EXPECTED_SCORE_OUTPUT
    assert exit_code == 0


def test_score_reports_an_unreadable_prediction_without_a_traceback(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = prediction_file(tmp_path, '{"fields": {"vendor_name": {"text": "Acme"}}}')

    exit_code = main(
        ["score", "syn0001", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert str(path) in captured.err


def test_score_reports_a_prediction_file_that_is_not_there(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.json"

    exit_code = main(
        ["score", "syn0001", "--prediction", str(absent), "--data-dir", str(data_dir)]
    )

    assert exit_code == 1
    assert str(absent) in capsys.readouterr().err


def corpus_report(data_dir: Path) -> str:
    """The whole report for the two synthetic documents, as the terminal prints it."""
    return "\n".join(
        [
            f"Corpus  {data_dir}, val",
            "  documents                2",
            "  KILE labels              8",
            "  LIR cells                7",
            "  line items               2",
            "  documents with no table  1",
            "  largest table            2",
            "",
            "Rules read, of the header labels",
            "  date    100.0%  1 of 1",
            "  number  100.0%  2 of 2",
            "  text    100.0%  5 of 5",
            "",
            "Rules read, of the line-item cells",
            "  number  100.0%  4 of 4",
            "  text    100.0%  3 of 3",
            "",
            "Fieldtypes (9)",
            "  amount_total_gross          number  2  100.0%",
            "  date_issue                  date    1  100.0%",
            # The number rule reads `SYN-0001` as -1, which is the whole reason
            # an identifier is text.
            "  document_id                 text    2          100.0% as a number",
            "  line_item_amount_gross      number  2  100.0%",
            "  line_item_description       text    2            0.0% as a number",
            "  line_item_quantity          number  2  100.0%",
            "  line_item_units_of_measure  text    1            0.0% as a number",
            "  vendor_address              text    1            0.0% as a number",
            "  vendor_name                 text    2            0.0% as a number",
            "",
            "metrics/normalization.py",
            "  numeric dates proving month first           0",
            "  numeric dates proving day first             0",
            "  dates the month rule's strictness turns on  0",
            "  numbers written with a lone dot and three digits (0)",
            "",
            "metrics/fields.py",
            "  header fieldtypes labeled more than once  0",
            "    of those, one value written twice       0",
            "  labels repeating a line inside one box    0",
            "",
            "metrics/line_items.py",
            "  rows repeating a fieldtype           0",
            "    of those, still after normalizing  0",
            "",
        ]
    )


def test_corpus_counts_the_dataset_under_the_module_that_asserts_each_count(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["corpus", "--data-dir", str(data_dir), "--split", "val"])

    assert capsys.readouterr().out == corpus_report(data_dir)
    assert exit_code == 0


def test_corpus_is_skipped_rather_than_failed_without_a_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI has no dataset, and a survey it cannot take is not a broken engine."""
    absent = tmp_path / "absent"

    exit_code = main(["corpus", "--data-dir", str(absent)])

    captured = capsys.readouterr()
    assert exit_code == 0, "a missing dataset must not fail the command"
    assert captured.out == f"Corpus\n  skipped, no dataset at {absent}\n"
    assert captured.err == ""


def test_corpus_still_reports_a_dataset_that_lacks_the_split(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A downloaded dataset missing `trainval` is wrong, not absent."""
    exit_code = main(["corpus", "--data-dir", str(data_dir)])

    assert exit_code == 1
    assert "trainval.json" in capsys.readouterr().err


EXPECTED_HEADER_ONLY_SCORE = "\n".join(
    [
        "Document syn0002",
        "",
        "Field score",
        "  precision  1.000",
        "  recall     0.333",
        "  F1         0.500",
        "  1 matched, 2 missing, 0 spurious",
        "",
        "Fields (3)",
        "  amount_total_gross  missing   40",
        "  document_id         missing   syn-0002",
        "  vendor_name         matched   synthetic supplies ltd",
        "",
        # No table on either side is an agreement about nothing, not a zero:
        # 355 of the 5,680 annotated documents carry no line items at all.
        "Line-item score",
        "  precision  1.000",
        "  recall     1.000",
        "  F1         1.000",
        "  0 matched, 0 missing, 0 spurious",
        "",
        "Cell accuracy (0)",
        "",
    ]
)


def test_score_handles_a_document_with_no_line_items(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = prediction_file(
        tmp_path, '{"fields": {"vendor_name": "Synthetic Supplies Ltd"}}'
    )

    exit_code = main(
        ["score", "syn0002", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    assert capsys.readouterr().out == EXPECTED_HEADER_ONLY_SCORE
    assert exit_code == 0


def test_score_reports_rows_predicted_for_a_document_that_has_none(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Recall is vacuously whole; precision is what an invented table costs."""
    path = prediction_file(
        tmp_path, '{"line_items": [{"line_item_description": "Invented"}]}'
    )

    exit_code = main(
        ["score", "syn0002", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "  0 matched, 0 missing, 1 spurious" in out
    assert "  line_item_description         0 of 0, 1 spurious" in out


def test_score_names_the_row_and_the_fieldtype_of_a_bad_cell(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Which half of a prediction is wrong is not enough to find the cell by."""
    path = prediction_file(
        tmp_path, '{"line_items": [{}, {"line_item_quantity": {"n": 2}}]}'
    )

    exit_code = main(
        ["score", "syn0001", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "line_items.1.line_item_quantity is the first problem" in captured.err


def test_score_reports_a_prediction_written_at_the_top_level(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fieldtypes outside "fields" would otherwise score as an empty prediction."""
    path = prediction_file(tmp_path, '{"vendor_name": "Acme"}')

    exit_code = main(
        ["score", "syn0001", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "vendor_name" in captured.err


def test_score_still_reports_the_dataset_errors_show_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = prediction_file(tmp_path, "{}")

    exit_code = main(
        [
            "score",
            "syn0001",
            "--prediction",
            str(path),
            "--data-dir",
            str(tmp_path / "absent"),
        ]
    )

    assert exit_code == 1
    assert "README.md" in capsys.readouterr().err


UCSF_IDS = tuple(f"doc{number:04d}" for number in range(0, 500, 2))
"""The half of `split_dir` whose public copies the archive publishes."""


@pytest.fixture
def split_dir(tmp_path: Path) -> Path:
    """A dataset whose val split is large enough to draw a subset from.

    Every other document comes from UCSF, which is the pool the subset is
    drawn from; the rest come from the FCC and are never ranked. Each page is
    US Letter at 200 dpi, which is what the archive in `archive` serves.
    """
    root = tmp_path / "dataset"
    ids = [f"doc{number:04d}" for number in range(500)]
    for document_id in ids:
        annotate(
            root,
            document_id,
            original_filename=f"u{document_id}xyz",
            page_sizes=[[1700, 2200]],
            source="ucsf" if document_id in UCSF_IDS else "pif",
        )
    (root / "val.json").write_text(json.dumps(ids), encoding="utf-8")
    return root


class Archive:
    """The UCSF archive, standing in for the network, with a copy per document.

    Every copy is one US Letter page, except the documents in `two_pages`,
    whose copies carry a page DocILE does not, and the ones in `missing`,
    which the archive will not serve.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.one_page = write_pdf(tmp_path / "one.pdf").read_bytes()
        self.two_pages_copy = write_pdf(tmp_path / "two.pdf", pages=2).read_bytes()
        self.two_pages: set[str] = set()
        self.missing: set[str] = set()
        self.asked: list[str] = []

    def fetch(self, ucsf_id: str) -> bytes:
        self.asked.append(ucsf_id)
        document_id = ucsf_id[1:8]
        if document_id in self.missing:
            raise FetchError(f"cannot fetch {ucsf_id}: HTTP Error 404")
        if document_id in self.two_pages:
            return self.two_pages_copy
        return self.one_page


@pytest.fixture
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Archive:
    stand_in = Archive(tmp_path)
    monkeypatch.setattr(public, "fetch", stand_in.fetch)
    return stand_in


def write_subset(path: Path, split_dir: Path, *flags: str) -> int:
    return main(
        ["subset", "--write", *flags, "--manifest", str(path)]
        + ["--data-dir", str(split_dir)]
    )


def test_subset_writes_the_manifest_it_draws(
    split_dir: Path,
    archive: Archive,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "subset.json"

    exit_code = write_subset(path, split_dir)

    assert exit_code == 0
    assert capsys.readouterr().out == "\n".join(
        [
            "Fixed subset",
            f"  manifest  {path}",
            "  split     val",
            "  source    ucsf",
            "  seed      20260912",
            "  size      100",
            "  rejected  0",
            "  written   yes",
            "",
        ]
    )
    written = load(path)
    assert written.document_ids == select(UCSF_IDS, seed=20260912, size=100)
    assert set(written.digests) == set(written.document_ids)


def test_subset_pins_every_reject_with_its_reason(
    split_dir: Path,
    archive: Archive,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "subset.json"
    ranked = rank(UCSF_IDS, seed=20260912)
    archive.two_pages.add(ranked[0])
    archive.missing.update(ranked[3:5])

    write_subset(path, split_dir)

    assert (
        "  rejected  3: 1 page count differs, 2 fetch failed\n"
        in capsys.readouterr().out
    )
    assert load(path).rejected == {
        ranked[0]: "page count differs",
        ranked[3]: "fetch failed",
        ranked[4]: "fetch failed",
    }


def test_subset_checks_the_pinned_list_without_the_network(
    split_dir: Path,
    archive: Archive,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "subset.json"
    archive.two_pages.add(rank(UCSF_IDS, seed=20260912)[1])
    write_subset(path, split_dir)
    capsys.readouterr()
    archive.asked.clear()

    exit_code = main(["subset", "--manifest", str(path), "--data-dir", str(split_dir)])

    assert exit_code == 0
    assert "  reproduced  yes" in capsys.readouterr().out
    assert archive.asked == []


def test_subset_fails_when_a_reject_is_no_longer_pinned(
    split_dir: Path,
    archive: Archive,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without its rejects the ranking draws a document the manifest left out."""
    path = tmp_path / "subset.json"
    archive.two_pages.add(rank(UCSF_IDS, seed=20260912)[1])
    write_subset(path, split_dir)
    capsys.readouterr()
    write(load(path).model_copy(update={"rejected": {}}), path)

    exit_code = main(["subset", "--manifest", str(path), "--data-dir", str(split_dir)])

    assert exit_code == 1
    assert "  reproduced  no, 1 of the 100 documents are not pinned" in (
        capsys.readouterr().out
    )


def test_subset_fails_when_the_seed_no_longer_draws_the_pinned_documents(
    split_dir: Path,
    archive: Archive,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A subset that drifted is not a benchmark, so this is not a warning."""
    path = tmp_path / "subset.json"
    write_subset(path, split_dir)
    capsys.readouterr()
    pinned = load(path)
    undrawn = next(each for each in UCSF_IDS if each not in pinned.document_ids)
    write(
        pinned.model_copy(
            update={
                "document_ids": (undrawn, *pinned.document_ids[1:]),
                "digests": {},
            }
        ),
        path,
    )

    exit_code = main(["subset", "--manifest", str(path), "--data-dir", str(split_dir)])

    assert exit_code == 1
    assert "  reproduced  no, 1 of the 100 documents are not pinned" in (
        capsys.readouterr().out
    )


def test_subset_reports_the_pinned_documents_in_the_wrong_order(
    split_dir: Path,
    archive: Archive,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A prefix of the subset is a sample, so the order is pinned too."""
    path = tmp_path / "subset.json"
    write_subset(path, split_dir)
    capsys.readouterr()
    pinned = load(path)
    write(
        pinned.model_copy(
            update={"document_ids": tuple(reversed(pinned.document_ids))}
        ),
        path,
    )

    exit_code = main(["subset", "--manifest", str(path), "--data-dir", str(split_dir)])

    assert exit_code == 1
    assert "  reproduced  no, the same documents in a different order" in (
        capsys.readouterr().out
    )


def test_subset_draws_with_the_seed_it_is_given(
    split_dir: Path,
    archive: Archive,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "subset.json"

    write_subset(path, split_dir, "--seed", "1")

    assert "  seed      1\n" in capsys.readouterr().out
    assert load(path).document_ids == select(UCSF_IDS, seed=1, size=100)


def test_subset_reports_a_manifest_that_is_not_there(
    split_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.json"

    exit_code = main(
        ["subset", "--manifest", str(absent), "--data-dir", str(split_dir)]
    )

    assert exit_code == 1
    assert str(absent) in capsys.readouterr().err


def test_subset_points_at_the_readme_when_the_dataset_is_not_downloaded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["subset", "--data-dir", str(tmp_path / "absent")])

    assert exit_code == 1
    assert "README.md" in capsys.readouterr().err


def test_subset_reports_a_split_the_dataset_does_not_have(
    split_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An incomplete download is a different problem to a drifted subset."""
    (split_dir / "val.json").unlink()

    exit_code = main(["subset", "--data-dir", str(split_dir)])

    assert exit_code == 1
    assert "val.json" in capsys.readouterr().err


def download(path: Path, split_dir: Path, copies: Path) -> int:
    return main(
        ["download", "--manifest", str(path), "--data-dir", str(split_dir)]
        + ["--copies", str(copies)]
    )


def test_download_puts_every_pinned_copy_in_the_copies_directory(
    split_dir: Path,
    archive: Archive,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "subset.json"
    copies = tmp_path / "ucsf"
    write_subset(path, split_dir, "--size", "3")
    capsys.readouterr()

    exit_code = download(path, split_dir, copies)

    assert exit_code == 0
    assert capsys.readouterr().out == "\n".join(
        [
            "Public copies, each verified against its digest",
            f"  manifest  {path}",
            f"  copies    {copies}",
            "  fetched   3",
            "  kept      0",
            "",
        ]
    )
    assert sorted(each.name for each in copies.iterdir()) == sorted(
        f"{each}.pdf" for each in load(path).document_ids
    )


def test_download_fails_loudly_when_a_copy_changed_in_the_archive(
    split_dir: Path,
    archive: Archive,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "subset.json"
    write_subset(path, split_dir, "--size", "3")
    capsys.readouterr()
    archive.one_page += b"\n% rescanned"

    exit_code = download(path, split_dir, tmp_path / "ucsf")

    assert exit_code == 1
    assert "but the manifest pins" in capsys.readouterr().err


EXPECTED_EVAL_OUTPUT = "\n".join(
    [
        "Fixed subset",
        "  manifest       {manifest}",
        "  split          val",
        "  size           5",
        "  predicted      4",
        "  not predicted  1",
        "  not pinned     1",
        "",
        # A pinned document nobody predicted and a predicted document nobody
        # pinned are both named, because a count cannot be acted on.
        "Not predicted (1)",
        "  eval0006",
        "",
        "Not pinned (1)",
        "  eval0001",
        "",
        # Scored on the reading plus its derived values.
        "Field score",
        "  precision  0.792",
        "  recall     0.704",
        "  F1         0.745",
        "  19 matched, 8 missing, 5 spurious",
        "",
        "Fields (9)",
        "  amount_due                1.000  2 matched, 0 missing",
        "  amount_total_gross        0.750  3 matched, 2 missing",
        "  currency_code_amount_due  0.667  1 matched, 0 missing, 1 spurious",
        "  date_due                  0.333  1 matched, 2 missing, 2 spurious",
        "  date_issue                0.857  3 matched, 1 missing",
        "  document_id               0.667  3 matched, 2 missing, 1 spurious",
        "  tax_detail_rate           1.000  2 matched, 0 missing",
        "  vendor_email              0.000  0 matched, 0 missing, 1 spurious",
        "  vendor_name               0.889  4 matched, 1 missing",
        "",
        # What code added, so the gain is not credited to the backend.
        "Derived values (1)",
        "  currency_code_amount_due  as read       0.000  0 matched, 1 missing",
        "                            with derived  0.667  1 matched, 0 missing, "
        "1 spurious",
        "",
        "Line-item score",
        "  precision  0.571",
        "  recall     0.571",
        "  F1         0.571",
        "  4 matched, 3 missing, 3 spurious",
        "",
        "Cell accuracy (3)",
        "  line_item_amount_gross  0.714  5 of 7, 2 spurious",
        "  line_item_description   0.857  6 of 7, 1 spurious",
        "  line_item_quantity      0.714  5 of 7, 1 spurious",
        "",
        # Ten fixed buckets, an empty one at n 0, and what came back with none.
        "Calibration, header values",
        "  confidence      n  accuracy",
        "  [0.0, 0.1)      0",
        "  [0.1, 0.2)      1     0.000",
        "  [0.2, 0.3)      0",
        "  [0.3, 0.4)      1     0.000",
        "  [0.4, 0.5)      0",
        "  [0.5, 0.6)      0",
        "  [0.6, 0.7)      1     1.000",
        "  [0.7, 0.8)      1     1.000",
        "  [0.8, 0.9)      2     1.000",
        "  [0.9, 1.0]     10     1.000",
        "  no confidence   6     0.667",
        "",
        "Calibration, line-item cells",
        "  confidence      n  accuracy",
        "  [0.0, 0.1)      0",
        "  [0.1, 0.2)      0",
        "  [0.2, 0.3)      0",
        "  [0.3, 0.4)      0",
        "  [0.4, 0.5)      1     0.000",
        "  [0.5, 0.6)      1     0.000",
        "  [0.6, 0.7)      0",
        "  [0.7, 0.8)      1     1.000",
        "  [0.8, 0.9)      4     0.750",
        "  [0.9, 1.0]     12     1.000",
        "  no confidence   1     0.000",
        "",
        # Precision only: a value never read carries no confidence.
        "Calibration speaks to precision only.",
        "",
        # Not checked counts on neither side of the rate, so n checked is beside it.
        "Gate",
        "  passed       1",
        "  failed       2",
        "  not checked  2",
        "  pass rate    0.333 of 3 checked",
        "",
        # Readings a rule could not read a value on, which it therefore did not check.
        "Unreadable",
        "  dates sane    1",
        "  totals agree  0",
        "",
        # Judged only on the values a checked rule used.
        "Gate ablation, over 3 checked",
        "  catches       1",
        "  misses        1",
        "  false alarms  1",
        "",
        # Wrong readings by what flagged them, and false alarms from each side.
        "Confidence sweep, over 3 checked",
        "  edge  gate only  confidence only  both  neither  gate false alarms  "
        "confidence false alarms",
        "  0.1           1                0     0        1                  1  "
        "                      0",
        "  0.2           0                0     1        1                  1  "
        "                      0",
        "  0.3           0                0     1        1                  1  "
        "                      0",
        "  0.4           0                1     1        0                  1  "
        "                      0",
        "  0.5           0                1     1        0                  1  "
        "                      0",
        "  0.6           0                1     1        0                  1  "
        "                      0",
        "  0.7           0                1     1        0                  1  "
        "                      0",
        "  0.8           0                1     1        0                  1  "
        "                      1",
        "  0.9           0                1     1        0                  1  "
        "                      1",
        "",
        # A gated value with no confidence never flags, so the readings holding
        # one are counted rather than read as unconfident.
        "  readings with a gated value with no confidence  1",
        "",
    ]
)
"""What CI sees. The counts behind it are checked in `evals/test_run.py`; this
pins the report that carries them."""


def evaluate(synthetic_subset: Path, *arguments: str) -> list[str]:
    return [
        "eval",
        "--data-dir",
        str(synthetic_subset),
        "--manifest",
        str(synthetic_subset / "subset.json"),
        "--predictions",
        str(synthetic_subset / "predictions.json"),
        *arguments,
    ]


def test_eval_reports_both_numbers_over_the_subset(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(evaluate(synthetic_subset))

    assert capsys.readouterr().out == EXPECTED_EVAL_OUTPUT.format(
        manifest=synthetic_subset / "subset.json"
    )
    assert exit_code == 0


def test_eval_derives_from_currency_symbols_saved_beside_the_predictions(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A saved Azure run is re-scored with its symbols, without being told to."""
    predictions = tmp_path / "predictions.json"
    predictions.write_bytes((synthetic_subset / "predictions.json").read_bytes())
    (tmp_path / "currency_symbols.json").write_text(
        '{"eval0005": {"amount_due": ["€"]}}', encoding="utf-8"
    )
    arguments = evaluate(synthetic_subset)
    arguments[arguments.index("--predictions") + 1] = str(predictions)

    exit_code = main(arguments)

    assert exit_code == 0
    assert "with derived  0.500  1 matched, 0 missing, 2 spurious" in (
        capsys.readouterr().out
    )


def test_eval_says_no_signal_for_a_run_with_no_confidence(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Gemini run saved no confidence file, and an empty one means the same."""
    predictions = tmp_path / "predictions.json"
    predictions.write_bytes((synthetic_subset / "predictions.json").read_bytes())
    (tmp_path / "confidence.json").write_text("{}", encoding="utf-8")
    arguments = evaluate(synthetic_subset)
    arguments[arguments.index("--predictions") + 1] = str(predictions)

    exit_code = main(arguments)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Calibration\n  no signal\n" in out
    assert "  confidence    no signal\n" in out
    assert "Confidence sweep" not in out


def test_eval_prints_no_label_text(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rule 6: the report is pasted into a commit message, so it carries counts."""
    main(evaluate(synthetic_subset))

    out = capsys.readouterr().out
    assert "Junction box" not in out
    assert "Beacon" not in out


def test_eval_scores_the_fixture_export_in_its_own_section(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The section CI's Eval step prints, over the export a replay loop on
    the fixture's loop run left after a review: its readings are not the
    scored run's, so nothing is skipped."""
    corrections = synthetic_subset / "corrections.json"
    exit_code = main(evaluate(synthetic_subset, "--corrections", str(corrections)))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.endswith(
        "\n".join(
            [
                "Corrections, never in the numbers above",
                f"  file       {corrections}",
                "  documents  2",
                "  skipped    0 made on this run's reading, on 0 documents",
                "",
                "Corrections by header field",
                "              n  read right  label agrees",
                "  amount_due  1           0             0",
                "  date_issue  1           0             1",
                "",
                "Corrections by line cell",
                "                          n  read right  label agrees",
                "  line_item_amount_gross  1           1             1",
                "  line_item_quantity      1           0             0",
                "",
                "Corrections by line",
                "                n  read right  label agrees",
                "  line added    1           0             0",
                "  line removed  1           0             0",
                "",
            ]
        )
    )
    assert "Junction box" not in out
    assert "Torque wrench" not in out


def test_eval_scores_the_fixed_subset_the_same_with_corrections(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Corrections never enter the fixed subset's field or line-item F1: the
    report without them is the report with them, up to their section."""
    main(evaluate(synthetic_subset))
    without = capsys.readouterr().out

    main(
        evaluate(
            synthetic_subset,
            "--corrections",
            str(synthetic_subset / "corrections.json"),
        )
    )
    with_them = capsys.readouterr().out

    assert with_them.startswith(without)
    assert "Corrections" not in without


def test_eval_reports_a_corrections_file_that_is_not_there(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.json"

    exit_code = main(evaluate(synthetic_subset, "--corrections", str(absent)))

    assert exit_code == 1
    assert f"cannot read the corrections {absent}" in capsys.readouterr().err


def test_corrections_exports_a_schema_with_none_as_an_empty_file(
    synthetic_subset: Path,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command's own path, on a schema nobody reviewed; what a review
    leaves is pinned by the pipeline's export tests."""
    schema = "corrections_cli_test"
    with connect(database_url) as connection:
        connection.autocommit = True
        drop_schema(connection, schema)
    prepare(database_url, schema)
    out = tmp_path / "corrections.json"

    exit_code = main(
        [
            "corrections",
            "--schema",
            schema,
            "--manifest",
            str(synthetic_subset / "runs" / "loop" / "manifest.json"),
            "--database-url",
            database_url,
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    assert "  documents    0\n  corrections  0\n" in capsys.readouterr().out
    assert json.loads(out.read_text("utf-8")) == {"schema": schema, "documents": []}


def test_eval_scores_the_committed_subset_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command in the README names a predictions file and nothing else."""
    predictions = tmp_path / "predictions.json"
    predictions.write_text("{}", encoding="utf-8")

    exit_code = main(
        [
            "eval",
            "--predictions",
            str(predictions),
            "--data-dir",
            str(tmp_path / "absent"),
        ]
    )

    assert exit_code == 1
    assert "README.md" in capsys.readouterr().err


def test_eval_reports_a_predictions_file_that_is_not_there(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.json"

    exit_code = main(
        [
            "eval",
            "--data-dir",
            str(synthetic_subset),
            "--manifest",
            str(synthetic_subset / "subset.json"),
            "--predictions",
            str(absent),
        ]
    )

    assert exit_code == 1
    assert str(absent) in capsys.readouterr().err


def test_eval_names_the_document_of_a_bad_prediction(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "predictions.json"
    path.write_text('{"eval0002": {"fields": {"vendor_name": 7}}}', encoding="utf-8")

    exit_code = main(
        [
            "eval",
            "--data-dir",
            str(synthetic_subset),
            "--manifest",
            str(synthetic_subset / "subset.json"),
            "--predictions",
            str(path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "eval0002.fields.vendor_name is the first problem" in captured.err


def test_eval_reports_a_pinned_document_the_dataset_does_not_hold(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dataset that cannot satisfy the manifest produces no number at all."""
    manifest = tmp_path / "subset.json"
    write(
        Manifest(
            split="val", seed=1, source="synthetic", size=1, document_ids=("eval9999",)
        ),
        manifest,
    )
    predictions = tmp_path / "predictions.json"
    predictions.write_text("{}", encoding="utf-8")

    exit_code = main(
        [
            "eval",
            "--data-dir",
            str(synthetic_subset),
            "--manifest",
            str(manifest),
            "--predictions",
            str(predictions),
        ]
    )

    assert exit_code == 1
    assert "eval9999" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--seed", "--size"])
def test_subset_refuses_to_draw_without_writing(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Checking the pinned seed while asked about another one is a wrong answer."""
    with pytest.raises(SystemExit):
        main(["subset", flag, "5"])

    assert "--write" in capsys.readouterr().err


def a_document(
    document_id: str,
    *,
    latency: float = 2.0,
    failure: str | None = None,
    served_model: str = "gemini-3.1-flash-lite",
) -> DocumentRun:
    read = failure is None
    return DocumentRun(
        document_id=document_id,
        pages=1,
        attempts=1 if read else 3,
        usage=Usage(
            input_tokens=1300 if read else 0, output_tokens=1100 if read else 0
        ),
        cost=Decimal("0.002") if read else Decimal(0),
        latency=latency,
        prediction=Prediction(fields={"vendor_name": ["Northwind"]}) if read else None,
        failure=failure,
        served_model=served_model if read else None,
    )


def a_run(*documents: DocumentRun) -> Run:
    return Run(
        backend="gemini",
        requested_model="gemini-3.1-flash-lite",
        manifest=Manifest(
            split="val",
            seed=1,
            source="synthetic",
            size=len(documents),
            document_ids=tuple(each.document_id for each in documents),
        ),
        long_edge=1600,
        documents=documents,
    )


def test_extract_reports_what_the_run_cost(tmp_path: Path) -> None:
    run = a_run(a_document("syn0001", latency=2.0), a_document("syn0002", latency=5.0))

    report = render_extract(tmp_path, run)

    assert "backend     gemini" in report
    assert "requested   gemini-3.1-flash-lite" in report
    assert "served      gemini-3.1-flash-lite" in report
    assert "predicted   2" in report
    assert "failed      0" in report
    assert "total          $0.0040" in report
    assert "per document   $0.002000" in report
    assert "input tokens   2,600" in report
    assert "p50  2.00 s" in report
    assert "p95  5.00 s" in report


def test_extract_reports_the_pages_a_per_page_backend_billed(tmp_path: Path) -> None:
    run = a_run(
        replace(a_document("syn0001"), usage=Usage(pages=3), cost=Decimal("0.03"))
    )

    assert "pages billed   3" in render_extract(tmp_path, run)


def test_extract_reports_the_cached_input_tokens_a_backend_reported(
    tmp_path: Path,
) -> None:
    run = a_run(
        replace(
            a_document("syn0001"),
            usage=Usage(
                input_tokens=1300, cached_input_tokens=1024, cache_write_tokens=276
            ),
        )
    )

    report = render_extract(tmp_path, run)
    assert "cached input   1,024" in report
    assert "cache writes   276" in report


def test_extract_says_a_backend_that_reads_the_pdf_rendered_nothing(
    tmp_path: Path,
) -> None:
    run = replace(a_run(a_document("syn0001")), backend="azure", long_edge=None)

    assert "long edge   not rendered" in render_extract(tmp_path, run)


def test_extract_names_every_model_the_vendor_said_it_served(tmp_path: Path) -> None:
    """A name pointed at a new version mid-run shows up here, not only in run.json."""
    run = a_run(
        a_document("syn0001"),
        a_document("syn0002", served_model="gemini-3.1-flash-lite-002"),
        a_document("syn0003", failure="gave up"),
    )

    report = render_extract(tmp_path, run)

    assert "served      gemini-3.1-flash-lite, gemini-3.1-flash-lite-002" in report


def test_extract_says_when_no_served_model_was_reported(tmp_path: Path) -> None:
    run = a_run(a_document("syn0001", failure="gave up"))

    assert "served      none reported" in render_extract(tmp_path, run)


def test_extract_on_openai_names_the_missing_key_before_making_the_run_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = tmp_path / "run"

    exit_code = main(
        [
            "extract",
            "--out",
            str(out),
            "--data-dir",
            str(tmp_path),
            "--backend",
            "openai",
        ]
    )

    assert exit_code == 1
    assert "no $OPENAI_API_KEY in the environment" in capsys.readouterr().err
    assert not out.exists()


def test_extract_on_azure_names_the_missing_endpoint_before_making_the_run_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", raising=False)
    out = tmp_path / "run"

    exit_code = main(
        [
            "extract",
            "--out",
            str(out),
            "--data-dir",
            str(tmp_path),
            "--backend",
            "azure",
        ]
    )

    assert exit_code == 1
    assert "no $AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT" in capsys.readouterr().err
    assert not out.exists()


def test_extract_refuses_a_backend_it_does_not_know(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        main(["extract", "--out", str(tmp_path), "--backend", "mistral"])

    assert "invalid choice" in capsys.readouterr().err


def test_extract_lists_the_documents_that_produced_nothing(tmp_path: Path) -> None:
    """A run that failed a fifth of the subset is broken, not low-scoring.

    Only the reasons say which kind of broken, and the same ids come back out
    of `docmatch eval` as its "not predicted" list.
    """
    run = a_run(
        a_document("syn0001"),
        a_document("syn0002", failure="the backend returned status 'failed'"),
    )

    report = render_extract(tmp_path, run)

    assert "failed      1" in report
    assert "Failed (1)" in report
    assert "syn0002  after 3, the backend returned status 'failed'" in report


def test_extract_charges_the_run_over_documents_that_failed_too(
    tmp_path: Path,
) -> None:
    run = a_run(a_document("syn0001"), a_document("syn0002", failure="gave up"))

    report = render_extract(tmp_path, run)

    assert "total          $0.0020" in report
    assert "per document   $0.001000" in report


def test_extract_says_which_key_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    exit_code = main(
        ["extract", "--out", str(tmp_path / "run"), "--data-dir", str(tmp_path)]
    )

    assert exit_code == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_extract_refuses_a_cost_cap_that_is_not_money(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Spanish-locale typo is an argparse message, not a traceback."""
    with pytest.raises(SystemExit):
        main(["extract", "--out", str(tmp_path), "--cost-cap", "0,05"])

    assert "invalid money value" in capsys.readouterr().err


def test_extract_refuses_a_count_below_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Otherwise `--attempts 0` reports a mistyped flag as 100 broken documents."""
    with pytest.raises(SystemExit):
        main(["extract", "--out", str(tmp_path), "--attempts", "0"])

    assert "invalid positive value" in capsys.readouterr().err


def test_extract_checks_it_can_write_before_it_spends(
    tmp_path: Path,
    pinned_copies: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An --out that cannot be made is worth catching before the first document."""
    fake = FakeExtractor(answers=[READING])
    monkeypatch.setattr(gemini, "extractor", lambda model, long_edge: fake)
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")

    exit_code = main(extracting(pinned_copies, blocked / "run"))

    assert exit_code == 1
    assert "cannot write the run to" in capsys.readouterr().err
    assert fake.attempts == []


@pytest.mark.parametrize("given", ["NaN", "Infinity", "0", "-1"])
def test_extract_refuses_a_cost_cap_that_is_not_a_positive_amount(
    given: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`Decimal` takes all four; a NaN cap would raise after the first document."""
    with pytest.raises(SystemExit):
        main(["extract", "--out", str(tmp_path), "--cost-cap", given])

    assert "invalid money value" in capsys.readouterr().err


@pytest.fixture
def pinned_copies(tmp_path: Path) -> Path:
    """Two pinned documents with their public copies in place, under `tmp_path`.

    The dataset in `docile`, the copies in `ucsf`, and the manifest pinning
    their digests at `manifest.json`, which is what this returns.
    """
    pinned = tmp_path / "manifest.json"
    write(a_subset(tmp_path).pinned, pinned)
    return pinned


def extracting(
    pinned: Path, out: Path, *flags: str, data_dir: Path | None = None
) -> list[str]:
    """The arguments of an extract over what `pinned_copies` made beside `pinned`."""
    root = pinned.parent
    return [
        "extract",
        "--out",
        str(out),
        "--data-dir",
        str(data_dir or root / "docile"),
        "--copies",
        str(root / "ucsf"),
        "--manifest",
        str(pinned),
        *flags,
    ]


def test_extract_keeps_the_documents_read_before_it_was_interrupted(
    tmp_path: Path,
    pinned_copies: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Ctrl-C on document ninety must not lose the eighty-nine paid for.

    What was read is written with a manifest covering exactly those documents,
    so their score is over what was read and their cost is on record.
    """
    fake = FakeExtractor(answers=[READING, KeyboardInterrupt()])
    monkeypatch.setattr(gemini, "extractor", lambda model, long_edge: fake)
    out = tmp_path / "run"

    exit_code = main(extracting(pinned_copies, out))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "interrupted after 1 of 2 documents" in captured.err
    assert list(read_predictions(out / "predictions.json")) == ["syn0001"]
    assert load(out / "manifest.json").document_ids == ("syn0001",)
    record = json.loads((out / "run.json").read_text())
    assert record["size"] == 1
    assert record["requested_model"] == "fake-001"
    assert [each["served_model"] for each in record["documents"]] == ["fake-002"]
    assert json.loads((out / "confidence.json").read_text()) == {}
    assert json.loads((out / "currency_symbols.json").read_text()) == {}


def test_extract_keeps_the_documents_read_before_an_unexpected_failure(
    tmp_path: Path, pinned_copies: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure nothing below expected still ends loudly, with the run on disk."""
    fake = FakeExtractor(answers=[READING, RuntimeError("nothing expected this")])
    monkeypatch.setattr(gemini, "extractor", lambda model, long_edge: fake)
    out = tmp_path / "run"

    with pytest.raises(RuntimeError):
        main(extracting(pinned_copies, out))

    assert list(read_predictions(out / "predictions.json")) == ["syn0001"]
    assert load(out / "manifest.json").size == 1


def test_extract_refuses_an_out_with_a_file_name_taken_by_a_directory(
    tmp_path: Path,
    pinned_copies: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Found before the first document, not when the write fails after the last."""
    fake = FakeExtractor(answers=[READING])
    monkeypatch.setattr(gemini, "extractor", lambda model, long_edge: fake)
    out = tmp_path / "run"
    (out / "predictions.json").mkdir(parents=True)

    exit_code = main(extracting(pinned_copies, out))

    assert exit_code == 1
    assert "predictions.json is a directory there" in capsys.readouterr().err
    assert fake.attempts == []


def test_extract_reports_a_missing_dataset_once(
    tmp_path: Path,
    pinned_copies: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One wrong --data-dir is one message, not a hundred documents with no labels."""
    fake = FakeExtractor(answers=[READING])
    monkeypatch.setattr(gemini, "extractor", lambda model, long_edge: fake)
    out = tmp_path / "run"

    exit_code = main(extracting(pinned_copies, out, data_dir=tmp_path / "docilee"))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "no DocILE dataset" in captured.err
    assert fake.attempts == []
    assert list(out.iterdir()) == []


def test_extract_refuses_a_model_with_no_price_before_making_the_run_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    out = tmp_path / "run"

    exit_code = main(
        [
            "extract",
            "--out",
            str(out),
            "--data-dir",
            str(tmp_path),
            "--model",
            "gemini-4-flash-lite-imaginary",
        ]
    )

    assert exit_code == 1
    assert "no price is written down" in capsys.readouterr().err
    assert not out.exists()


def test_extract_refuses_to_start_on_a_missing_public_copy(
    tmp_path: Path,
    pinned_copies: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Named once, before a request is sent or the run directory is made."""
    fake = FakeExtractor(answers=[READING])
    monkeypatch.setattr(gemini, "extractor", lambda model, long_edge: fake)
    out = tmp_path / "run"
    arguments = extracting(pinned_copies, out)
    public.path(tmp_path / "ucsf", "syn0002").unlink()

    exit_code = main(arguments)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "no public copy of syn0002" in captured.err
    assert fake.attempts == []
    assert not out.exists()


def test_extract_refuses_to_start_on_a_changed_public_copy(
    tmp_path: Path,
    pinned_copies: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeExtractor(answers=[READING])
    monkeypatch.setattr(gemini, "extractor", lambda model, long_edge: fake)
    out = tmp_path / "run"
    arguments = extracting(pinned_copies, out)
    write_pdf(public.path(tmp_path / "ucsf", "syn0001"), pages=4)

    exit_code = main(arguments)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "public copy of syn0001" in captured.err
    assert "the manifest pins" in captured.err
    assert fake.attempts == []
    assert not out.exists()


def test_extract_reads_a_limited_prefix_end_to_end(
    tmp_path: Path,
    pinned_copies: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A `--limit` run verifies, reads and writes only the documents it covers."""
    built: dict[str, object] = {}
    fake = FakeExtractor(answers=[READING])

    def backend(model: str, long_edge: int) -> FakeExtractor:
        built.update(model=model, long_edge=long_edge)
        return fake

    monkeypatch.setattr(gemini, "extractor", backend)
    out = tmp_path / "run"
    arguments = extracting(pinned_copies, out, "--limit", "1", "--long-edge", "1200")
    # Beyond the limit, so a run that verified the whole subset would refuse.
    public.path(tmp_path / "ucsf", "syn0002").unlink()

    exit_code = main(arguments)

    assert exit_code == 0, capsys.readouterr().err
    assert built == {"model": gemini.MODEL, "long_edge": 1200}
    assert [each.document_id for each in fake.attempts] == ["syn0001"]
    assert fake.attempts[0].path == tmp_path / "ucsf" / "syn0001.pdf"
    assert list(read_predictions(out / "predictions.json")) == ["syn0001"]
    assert load(out / "manifest.json").document_ids == ("syn0001",)
    record = json.loads((out / "run.json").read_text())
    assert record["backend"] == "gemini"
    assert record["requested_model"] == "fake-001"
    assert record["long_edge"] == 1200
    assert [each["pages"] for each in record["documents"]] == [1]
    assert [each["served_model"] for each in record["documents"]] == ["fake-002"]


def matching(synthetic_subset: Path, *arguments: str) -> list[str]:
    return [
        "match",
        "--data-dir",
        str(synthetic_subset),
        "--manifest",
        str(synthetic_subset / "subset.json"),
        *arguments,
    ]


MATCH_FLOORS = [
    # The fixture's train split is two documents of eight lines, six of
    # them the same item in both, the six entries the resolution catalog
    # is derived from; its val split is the five the manifest pins, one
    # of which has no lines and seeds nothing. Only train's first two
    # lines per document carry codes, and only its lines carry units.
    "Seed pool",
    "  pool   documents  lines  without lines",
    "  train          2     16              0",
    "  val            5      7              1",
    "",
    # The procedure pairs lines of different train documents only, drawn
    # from the lines carrying the cell. Four lines carry a code, two per
    # document, so every code pair is one of four, each drawn about a
    # quarter of the time: the closest, ST-120 against HT-204 or HT-207,
    # agree at exactly 0.5, which clears 0.5, so the code floor is 0.6.
    # Every line carries a description, and the six shared items pair
    # identically about six times in sixty-four, far past the one percent
    # a step may let clear, so no step holds and the procedure gives none.
    "Pairing floors, each constant and the procedure's value on train",
    "  cell         constant  procedure  pairs",
    "  code              0.5        0.6  10000",
    "  description       0.4       none  10000",
]

MATCH_TABLES = [
    "",
    # Every fixture row carries an amount, and the shared train lines a
    # unit price too, so a price variance falls to the amount except on a
    # pair of those. Each type starts 500 cases and the discrepancies
    # mixed into them take it past 500. A quantity of one unit carries no
    # short-ship or over-ship, which leaves one document without either.
    # Labels find every discrepancy but where a case removes a shared
    # train line and adds the same item from the other train document as
    # the donor: the two pair on their identical description and agree on
    # every number, so the extra line and the missing line are missed and
    # nothing false fires.
    "Per-type table, over cases from train and val",
    "  type            precision  recall     n  documents",
    "  price variance      1.000   1.000  1050          6",
    "  short-ship          1.000   1.000   931          5",
    "  over-ship           1.000   1.000   980          5",
    "  extra line          1.000   0.998  1005          6",
    "  missing line        1.000   0.999  1628          6",
    # Both train documents label units and one a header tax, drawn afresh
    # for every case they start and every one they are mixed into.
    "  unit variant        1.000   1.000   798          2",
    "  tax mismatch        1.000   1.000   590          1",
    "  clean-case false-positive rate  0.000 of 1000 cases",
    "",
    # No hard negative fires on labels, and nothing fires elsewhere: the
    # one pairing that hides a finding agrees on every number.
    "Diagnostic table, over the same cases",
    "  hard negative   placed  false alarms",
    "  rounding drift    4406             0",
    "  just inside       3071             0",
    "  billed below      4408             0",
    "  false alarms on no hard negative   0",
    # That pairing joins a removed line to its donor, which the key names
    # no invoice line for, so it crosses no pair: every pair the key does
    # name is the one the matcher made (#102).
    "  lines paired with another partner  0 of 21362, 0 alike on code, description, "
    "quantity, unit price and amount",
    "",
    "  type            near recall    n  far recall    n",
    "  price variance        1.000  525       1.000  525",
    "  short-ship            1.000  466       1.000  465",
    "  over-ship             1.000  490       1.000  490",
    "  tax mismatch          1.000  295       1.000  295",
    "  headline recall is the half-and-half mix of near and far",
    "",
    "Labels control, over cases from the fixed subset",
    "  manifest                        {manifest}",
    "  documents                       4 of 5 with lines",
    "  precision                       1.000",
    "  recall                          1.000",
    "  clean-case false-positive rate  0.000 of 1000 cases",
    "",
    # The pinned documents carry no code and no two of their descriptions
    # agree at the floor, so a removed line never pairs with a donor line.
    "  type                      precision  recall     n  documents",
    "  price variance                1.000   1.000   884          4",
    "  short-ship                    1.000   1.000   714          3",
    "  over-ship                     1.000   1.000   749          3",
    "  extra line                    1.000   1.000   897          4",
    "  missing line                  1.000   1.000  1794          4",
    # No pinned document labels a unit or a header tax: n 0, not an
    # error. A row over the fixed subset marks both indicative (#67).
    "  unit variant, indicative      1.000   1.000     0          0",
    "  tax mismatch, indicative      1.000   1.000     0          0",
]

EXPECTED_MATCH_OUTPUT = "\n".join([*MATCH_FLOORS, *MATCH_TABLES, ""])

EXPECTED_MATCH_RUN_OUTPUT = "\n".join(
    [
        *MATCH_FLOORS,
        "",
        # Over one clean case per document: eval0003's three rows, read out
        # of order, eval0004's one, and eval0005's two with a label; its
        # made-up delivery line pairs with no labeled row, and eval0006 has
        # no reading. Every description read agrees with its label, so the
        # floor leaves none unpaired.
        "Floor cost per run, over one clean case per document",
        "  run                   lines the metric pairs  floor cost",
        "  gemini synthetic-001                       6           0",
        *MATCH_TABLES,
        "",
        # The same cases as the control, eval0006's with no reading at all.
        "End-to-end row, gemini synthetic-001, its readings as the invoices",
        "  run                             {run}",
        "  documents                       4 of 5 with lines",
        "  precision                       0.722",
        "  recall                          0.875",
        # Every clean case of eval0005 bills the made-up line and every one
        # of eval0006 misses both its lines: two of the four documents.
        "  clean-case false-positive rate  0.500 of 1000 cases",
        "",
        "  type                      precision  recall     n  documents",
        # eval0005's junction box is read at a tenth of its amount, so a
        # price variance there is hidden, and eval0006's are all missed.
        "  price variance                1.000   0.681   884          4",
        "  short-ship                    1.000   0.863   714          3",
        "  over-ship                     1.000   0.862   749          3",
        # eval0003's rows are read out of order, and its extra lines are
        # still found once placed through the line-item assignment; only
        # eval0006's are missed. eval0005's made-up line is the false alarm
        # on every one of its cases, so the more findings a case carries,
        # the higher the precision.
        "  extra line                    0.420   0.837   897          4",
        # A document with no reading leaves every purchase-order line
        # unpaired: the missing line is found, with the others beside it.
        "  missing line                  0.731   1.000  1794          4",
        "  unit variant, indicative      1.000   1.000     0          0",
        "  tax mismatch, indicative      1.000   1.000     0          0",
        "",
    ]
)


def test_match_reports_the_per_type_table_and_the_labels_control(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(matching(synthetic_subset))

    assert capsys.readouterr().out == EXPECTED_MATCH_OUTPUT.format(
        manifest=synthetic_subset / "subset.json"
    )
    assert exit_code == 0


def test_match_prints_no_label_text(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rule 6: cases are built from labels, and the report carries counts only."""
    main(matching(synthetic_subset))

    out = capsys.readouterr().out
    assert "Junction box" not in out
    assert "Beacon" not in out
    assert "317.50" not in out


def test_match_reports_a_missing_split_without_a_traceback(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The seed pool is fixed in code, so a partial download is an error, not a
    smaller table."""
    partial = tmp_path / "docile"
    shutil.copytree(synthetic_subset, partial)
    (partial / "train.json").unlink()

    exit_code = main(matching(partial))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "train.json" in captured.err


def test_match_reports_a_cell_no_two_train_documents_carry_as_no_pairs(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A floor is measured on lines of two different documents, so a split
    with one document carrying the cell has nothing to measure it on."""
    lone = tmp_path / "docile"
    shutil.copytree(synthetic_subset, lone)
    (lone / "train.json").write_text('["eval0001"]', encoding="utf-8")

    exit_code = main(matching(lone))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "  code              0.5       none      0\n" in out
    assert "  description       0.4       none      0\n" in out


def test_match_reports_a_cell_no_step_keeps_under_its_share_as_none(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two train documents listing the same lines pair identically about half
    the time, which clears even 1.0, the top of the grid."""
    twins = tmp_path / "docile"
    shutil.copytree(synthetic_subset, twins)
    annotations = twins / "annotations"
    shutil.copy(annotations / "eval0007.json", annotations / "eval0008.json")
    (twins / "train.json").write_text('["eval0007", "eval0008"]', encoding="utf-8")

    exit_code = main(matching(twins))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "  code              0.5       none  10000\n" in out
    assert "  description       0.4       none  10000\n" in out


def test_match_refuses_a_manifest_that_pins_a_train_document(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The floors are measured on train and never on the fixed subset, so the
    two may not share a document."""
    manifest = tmp_path / "subset.json"
    write(
        Manifest(
            split="train",
            seed=1,
            source="synthetic",
            size=1,
            document_ids=("eval0001",),
        ),
        manifest,
    )

    exit_code = main(
        ["match", "--data-dir", str(synthetic_subset), "--manifest", str(manifest)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "eval0001" in captured.err


def test_match_adds_an_end_to_end_row_for_a_saved_run(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(matching(synthetic_subset, "--run", str(synthetic_subset)))

    assert capsys.readouterr().out == EXPECTED_MATCH_RUN_OUTPUT.format(
        manifest=synthetic_subset / "subset.json", run=synthetic_subset
    )
    assert exit_code == 0


def a_saved_run(synthetic_subset: Path, where: Path, backend: str) -> Path:
    """The fixture's saved run, copied, under another backend's name."""
    where.mkdir()
    for name in ("predictions.json", "manifest.json"):
        shutil.copy(synthetic_subset / name, where / name)
    (where / "run.json").write_text(
        json.dumps({"backend": backend, "requested_model": "synthetic-002"}),
        encoding="utf-8",
    )
    return where


def test_match_adds_one_row_per_run_named_from_its_record(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = a_saved_run(synthetic_subset, tmp_path / "azure", "azure")

    exit_code = main(
        matching(synthetic_subset, "--run", str(synthetic_subset), "--run", str(other))
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.index("End-to-end row, gemini synthetic-001") < out.index(
        "End-to-end row, azure synthetic-002"
    )
    # The same readings on the same cases give the same row.
    first, second = out.split("End-to-end row, ")[1:]
    assert first.strip().splitlines()[2:] == second.strip().splitlines()[2:]


def test_match_refuses_a_run_over_another_subset(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `--limit` run covers a prefix of the pinned subset, and never becomes
    a row (#75)."""
    limited = a_saved_run(synthetic_subset, tmp_path / "limited", "gemini")
    write(load(synthetic_subset / "subset.json").first(3), limited / "manifest.json")

    exit_code = main(matching(synthetic_subset, "--run", str(limited)))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert str(limited / "manifest.json") in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("name", ["predictions.json", "manifest.json", "run.json"])
def test_match_reports_a_run_missing_one_of_its_files(
    synthetic_subset: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    broken = a_saved_run(synthetic_subset, tmp_path / "broken", "gemini")
    (broken / name).unlink()

    exit_code = main(matching(synthetic_subset, "--run", str(broken)))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert name in captured.err


def test_match_prints_no_reading_text(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rule 6: the readings are document content too; the rows carry counts."""
    main(matching(synthetic_subset, "--run", str(synthetic_subset)))

    out = capsys.readouterr().out
    assert "Delivery" not in out
    assert "42.00" not in out


def test_match_writes_nothing(synthetic_subset: Path, tmp_path: Path) -> None:
    before = sorted(each.name for each in synthetic_subset.iterdir())

    main(matching(synthetic_subset, "--run", str(synthetic_subset)))

    assert sorted(each.name for each in synthetic_subset.iterdir()) == before
    assert list(tmp_path.iterdir()) == []


def resolving(data_dir: Path, *arguments: str) -> list[str]:
    return ["resolve", "--data-dir", str(data_dir), *arguments]


UNANSWERING = "postgresql://localhost:1/nothing"
"""A database url no server answers at: port 1 refuses at once."""


def slice_of(name: SliceName, entries: int, out: tuple[int, int] = (0, 0)) -> Slice:
    """A slice of `entries` entries, each with its exact query and two
    variants, and `out` exact and noisy out-of-catalog queries; the texts
    are placeholders, since the renderer prints none."""
    queries = []
    for index in range(entries):
        queries.append(Query("x", f"SKU-{index}", "exact"))
        queries.append(Query("x", f"SKU-{index}", "extra words"))
        queries.append(Query("x", f"SKU-{index}", "digits dropped"))
    exact, noisy = out
    out_of_catalog = [Query("x", None, "exact")] * exact
    out_of_catalog += [Query("x", None, "punctuation")] * noisy
    return Slice(name, tuple(queries), tuple(out_of_catalog))


def query_set_of(
    scored: int,
    development: int,
    kinds: Sequence[int],
    bands: Sequence[int],
    out: tuple[tuple[int, int], tuple[int, int]] = ((0, 0), (0, 0)),
) -> QuerySet:
    """A query set with the counts given, its shares over the noisy variants."""
    noisy = 2 * (scored + development)
    return QuerySet(
        slice_of("scored", scored, out[0]),
        slice_of("development", development, out[1]),
        tuple(
            Share(kind, count, count / noisy if noisy else None, KIND_SHARES[kind])
            for kind, count in zip(NOISE_KINDS, kinds, strict=True)
        ),
        tuple(
            Share(name, count, count / noisy if noisy else None, target)
            for (name, _, _, target), count in zip(BAND_TARGETS, bands, strict=True)
        ),
    )


EXPECTED_RESOLVE_OUTPUT = "\n".join(
    [
        "Catalog, from train",
        "  documents              5180",
        "  lines                  36147",
        "  distinct descriptions  8855",
        "  entries                1920",
        "",
        "Query set, one exact query and 2 noisy variants per entry, one entry "
        "in 5 to development",
        "  slice        entries  exact  noisy  queries",
        "  scored          1536   1536   3072     4608",
        "  development      384    384    768     1152",
        "",
        "Out of catalog, one train singleton each below 0.95 to every entry, "
        "0.150 of the set against a target of 0.150",
        "  slice        exact  noisy  queries",
        "  scored         271    542      813",
        "  development     68    135      203",
        "",
        "Noise model, over every noisy variant, each share against its measured target",
        "  kind                 share  target",
        "  extra words          0.361   0.350",
        "  letters substituted  0.242   0.245",
        "  digits dropped       0.218   0.212",
        "  punctuation          0.178   0.192",
        "",
        "  similarity to the entry  share  target",
        "  [0.9, 1.0)               0.359   0.362",
        "  [0.8, 0.9)               0.141   0.095",
        "  [0.7, 0.8)               0.081   0.126",
        "  [0.5, 0.7)               0.138   0.095",
        "  below 0.5                0.281   0.322",
        "",
        "Headline, over the scored slice, exact weight w = 0.667",
        "  arm      top-1  top-5     n",
        "  trigram  0.922  0.980  4608",
        "  vector   0.946  0.989  4608",
        "  hybrid   0.947  0.992  4608",
        "",
        "Separability, top-1 score, 4608 answerable against 813 out of catalog, "
        "both weighted by w",
        "  arm      rejected at 0.99  rejected at 0.95  rejected at 0.90  AUROC",
        "  trigram             0.101             0.352             0.498  0.874",
        "  vector              0.050             0.210             0.330  0.795",
        "  hybrid              0.080             0.301             0.440  0.851",
        "  each cut keeps that share of the arm's own answerable scores; a "
        "reporting device, no threshold is chosen",
        "",
        "Per arm",
        "  arm      rank-1 tie rate  p50 ms  p95 ms  full fetches  "
        "boundary equal to cut  short fetches",
        "  trigram            0.065    0.41    0.99          4600  "
        "                    3              8",
        "  vector             0.017   14.21   22.83          4608  "
        "                    0              0",
        "  hybrid             0.033   14.95   23.46          4608  "
        "                    2              0",
        "  latency over every scored query, answerable and out of catalog; "
        "fetches over the answerable ones",
        "  a boundary equal to the cut means a tie group may reach past the "
        "fetched rows; a short fetch has nothing past them",
        "",
        "Paid once, outside the latency",
        "  model load         2.71 s",
        "  catalog embedding  1.23 s",
        "  hnsw build         0.46 s",
        "",
        "Sweep over d per hybrid half, development top-5 at each value",
        "  d    top-5     n",
        "  25   0.991  1152",
        "  50   0.993  1152",
        "  100  0.994  1152",
        "  constant d, the scored slice's  50",
        "  procedure's d                   25",
        "  rule: the smallest d whose development top-5 is within 0.010 of the "
        "grid's best, the smaller on a tie",
        "",
        "Operating threshold, the hybrid's top-1 score keeping 0.95 of the "
        "development slice's answerable queries, weighted by w",
        "  constant, the loop's  0.031545",
        "  procedure's value     0.032522",
        "  n                     1152",
        "  a line at or above it carries its top-1 SKU, one below has no entry; "
        "it annotates and never routes",
        "",
        "By kind, the same scored queries regrouped, report only",
        "  kind                    n  trigram top-1  trigram top-5  "
        "vector top-1  vector top-5  hybrid top-1  hybrid top-5",
        "  exact                1536          0.970          0.999  "
        "       0.977         0.999         0.973         1.000",
        "  extra words          1110          0.811          0.901  "
        "       0.856         0.946         0.865         0.955",
        "  letters substituted   744          0.941          0.995  "
        "       0.968         0.997         0.974         0.999",
        "  digits dropped        670          0.597          0.896  "
        "       0.746         0.955         0.776         0.970",
        "  punctuation           548          0.985          1.000  "
        "       0.995         1.000         0.996         1.000",
        "",
        "Provenance, read at run time",
        "  postgres               16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)",
        "  pgvector               0.6.0",
        "  pg_trgm                1.6",
        "  hnsw ef_search         100",
        "  embedder               sentence-transformers/all-MiniLM-L6-v2",
        "  embedder revision      1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "  sentence-transformers  6.1.0",
        "  torch                  2.14.0+cpu",
        "  torch threads          10",
        "  cpu                    AMD Ryzen 7 5800H with Radeon Graphics",
        "  logical cpus           10",
        "  memory                 11.7 GiB",
        "  kernel                 6.18.33.2-microsoft-standard-WSL2",
        "",
    ]
)


def test_resolve_renders_the_report_from_a_built_result() -> None:
    """The `render_extract` pattern: the numbers are the run's, the block is
    this module's, and the CLI tests need no database. The headline is
    `w` times the exact rate plus `1 - w` times the noisy rate: 0.970 and
    0.826 at top-1 give 0.922, 0.999 and 0.942 at top-5 give 0.980 for the
    trigram arm; 0.977 and 0.884 give 0.946, 0.999 and 0.970 give 0.989 for
    the vector arm; 0.973 and 0.896 give 0.947, 1.000 and 0.977 give 0.992
    for the hybrid. The sweep's constant is 50 and its procedure 25, since
    0.991 is within a point of 0.994, so the disagreement shows."""
    result = resolution.ResolveResult(
        CatalogCounts(documents=5180, lines=36147, distinct=8855, entries=1920),
        query_set_of(
            1536,
            384,
            kinds=(1388, 930, 837, 685),
            bands=(1380, 541, 311, 530, 1078),
            out=((271, 542), (68, 135)),
        ),
        resolution.Measured(
            (
                resolution.ArmResult(
                    "trigram",
                    (
                        resolution.KindScore("exact", 1536, 1490, 1535),
                        resolution.KindScore("extra words", 1110, 900, 1000),
                        resolution.KindScore("letters substituted", 744, 700, 740),
                        resolution.KindScore("digits dropped", 670, 400, 600),
                        resolution.KindScore("punctuation", 548, 540, 548),
                    ),
                    4608,
                    300,
                    0.412,
                    0.987,
                    resolution.OverFetch(full=4600, equal=3, short=8),
                    resolution.Separability((0.1012, 0.3521, 0.4979), 0.8744),
                ),
                resolution.ArmResult(
                    "vector",
                    (
                        resolution.KindScore("exact", 1536, 1500, 1534),
                        resolution.KindScore("extra words", 1110, 950, 1050),
                        resolution.KindScore("letters substituted", 744, 720, 742),
                        resolution.KindScore("digits dropped", 670, 500, 640),
                        resolution.KindScore("punctuation", 548, 545, 548),
                    ),
                    4608,
                    80,
                    14.212,
                    22.834,
                    resolution.OverFetch(full=4608, equal=0, short=0),
                    resolution.Separability((0.05, 0.21, 0.33), 0.795),
                ),
                resolution.ArmResult(
                    "hybrid",
                    (
                        resolution.KindScore("exact", 1536, 1495, 1536),
                        resolution.KindScore("extra words", 1110, 960, 1060),
                        resolution.KindScore("letters substituted", 744, 725, 743),
                        resolution.KindScore("digits dropped", 670, 520, 650),
                        resolution.KindScore("punctuation", 548, 546, 548),
                    ),
                    4608,
                    150,
                    14.953,
                    23.461,
                    resolution.OverFetch(full=4608, equal=2, short=0),
                    resolution.Separability((0.0801, 0.3012, 0.4401), 0.8512),
                ),
            ),
            Sweep(
                "d",
                50,
                (
                    Point(25, 0.99124, 1152),
                    Point(50, 0.99312, 1152),
                    Point(100, 0.99401, 1152),
                ),
            ),
            resolution.Operating(1 / 61 + 1 / 66, 1 / 61 + 1 / 62, 1152),
            ServerVersions(
                "16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)", "0.6.0", "1.6", "100"
            ),
            ModelVersions(
                "sentence-transformers/all-MiniLM-L6-v2",
                "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
                "6.1.0",
                "2.14.0+cpu",
                10,
            ),
            2.714,
            Build(embedding_s=1.234, index_s=0.456),
            Machine(
                "AMD Ryzen 7 5800H with Radeon Graphics",
                10,
                11.68,
                "6.18.33.2-microsoft-standard-WSL2",
            ),
        ),
    )

    assert render_resolve(result) == EXPECTED_RESOLVE_OUTPUT


def test_resolve_renders_an_empty_catalog_as_nothing_to_resolve() -> None:
    result = resolution.ResolveResult(
        CatalogCounts(documents=2, lines=3, distinct=3, entries=0),
        query_set_of(0, 0, kinds=(0, 0, 0, 0), bands=(0, 0, 0, 0, 0)),
        None,
    )

    assert render_resolve(result) == "\n".join(
        [
            "Catalog, from train",
            "  documents              2",
            "  lines                  3",
            "  distinct descriptions  3",
            "  entries                0",
            "",
            "Query set, one exact query and 2 noisy variants per entry, one "
            "entry in 5 to development",
            "  slice        entries  exact  noisy  queries",
            "  scored             0      0      0        0",
            "  development        0      0      0        0",
            "",
            "Out of catalog, one train singleton each below 0.95 to every entry, "
            "none of the set against a target of 0.150",
            "  slice        exact  noisy  queries",
            "  scored           0      0        0",
            "  development      0      0        0",
            "",
            "Noise model, over every noisy variant, each share against its "
            "measured target",
            "  kind                 share  target",
            "  extra words           none   0.350",
            "  letters substituted   none   0.245",
            "  digits dropped        none   0.212",
            "  punctuation           none   0.192",
            "",
            "  similarity to the entry  share  target",
            "  [0.9, 1.0)                none   0.362",
            "  [0.8, 0.9)                none   0.095",
            "  [0.7, 0.8)                none   0.126",
            "  [0.5, 0.7)                none   0.095",
            "  below 0.5                 none   0.322",
            "",
            "Nothing to resolve: no description appears in two or more train "
            "documents, so the database was not touched.",
            "",
        ]
    )


def test_resolve_reports_a_missing_train_split_without_a_traceback(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The catalog seeds from train alone, so a partial download is an error
    and not a smaller catalog; it is reported before the database is touched."""
    partial = tmp_path / "docile"
    shutil.copytree(synthetic_subset, partial)
    (partial / "train.json").unlink()

    exit_code = main(resolving(partial, "--database-url", UNANSWERING))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "train.json" in captured.err


def test_resolve_reports_a_database_that_does_not_answer_without_a_traceback(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        resolving(
            synthetic_subset,
            "--database-url",
            "postgresql://someone:secret@localhost:1/nothing",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith("docmatch: no database answers at ")
    assert "dbname=nothing" in captured.err
    assert "secret" not in captured.err


def test_resolve_takes_the_database_url_from_the_flag_before_the_environment(
    synthetic_subset: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DOCMATCH_DATABASE_URL", "postgresql://localhost:1/fromenv")

    main(resolving(synthetic_subset))
    assert "dbname=fromenv" in capsys.readouterr().err

    main(resolving(synthetic_subset, "--database-url", "postgresql://localhost:1/flag"))
    assert "dbname=flag" in capsys.readouterr().err


def test_resolve_prints_the_fixture_report_with_no_label_text(
    synthetic_subset: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real path over the fixture, the one CI's Resolve step runs with
    the real models: the six descriptions the two train documents share are
    the catalog, the split leaves one development entry, the scored slice's
    fifteen queries are measured on every arm, the depth sweep runs over the
    development entry's three, and rule 6 holds on the output. The run is
    pointed at the test schema so a real run's `resolution` is left for
    inspection, and at the fake embedder so no weights are loaded; the
    command itself has a flag for neither."""
    monkeypatch.setattr(
        resolution, "resolve", partial(resolution.resolve, schema=TEST_SCHEMA)
    )
    monkeypatch.setattr(cli, "load_models", fake_loader)
    exit_code = main(resolving(synthetic_subset, "--database-url", database_url))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.startswith(
        "\n".join(
            [
                "Catalog, from train",
                "  documents              2",
                "  lines                  16",
                "  distinct descriptions  10",
                "  entries                6",
                "",
                "Query set, one exact query and 2 noisy variants per entry, "
                "one entry in 5 to development",
                "  slice        entries  exact  noisy  queries",
                "  scored             5      5     10       15",
                "  development        1      1      2        3",
                "",
                "Out of catalog, one train singleton each below 0.95 to every "
                "entry, 0.143 of the set against a target of 0.150",
                "  slice        exact  noisy  queries",
                "  scored           1      2        3",
                "  development      0      0        0",
                "",
                "Noise model, over every noisy variant, each share against its "
                "measured target",
            ]
        )
    )
    separability = "Separability, top-1 score, 15 answerable against 3 out of catalog"
    assert separability in out
    for arm in ("trigram", "vector"):
        assert re.search(rf"\n  {arm} +(\d\.\d{{3}} +){{3}}\d\.\d{{3}}\n", out), arm
    assert "\n  trigram  1.000  1.000  15\n" in out, (
        "every scored query finds its entry"
    )
    assert "\n  vector   1.000  1.000  15\n" in out, (
        "a one-hot fake still puts the exact entry first"
    )
    assert "\n  hybrid   1.000  1.000  15\n" in out
    assert "\n  25   1.000  3\n" in out, "the sweep runs over the one development entry"
    assert "\n  procedure's d                   25\n" in out
    assert "\n  procedure's value     0.032787\n" in out, (
        "every development query is first in both halves, 2/61"
    )
    assert "\n  exact                5          1.000          1.000" in out
    assert "\n  model load         0.50 s\n" in out
    assert "Provenance, read at run time" in out
    assert f"\n  embedder               {FAKE_VERSIONS.embedder}\n" in out
    assert "\n  torch threads          1\n" in out
    assert_no_label_text(synthetic_subset, out)


def assert_no_label_text(synthetic_subset: Path, out: str) -> None:
    """No description the fixture's train split labels, entry or singleton,
    and no word of one, appears in the output."""
    dataset = DocileDataset(synthetic_subset)
    descriptions = {
        text
        for document_id in dataset.document_ids("train")
        for row in labeled_line_items(dataset.annotation(document_id))
        for text in row.get("line_item_description", ())
    }
    assert len(descriptions) == 10
    lowered = out.casefold()
    for description in descriptions:
        assert description.casefold() not in lowered
        for word in description.replace(",", " ").split():
            if len(word) > 3:
                assert word.casefold() not in lowered, word


def test_pipeline_prints_the_ladder_from_p0_to_p4() -> None:
    out = render_pipeline(
        [(Path("runs/a"), report(loop_run(approved("a", 0), failed_extraction("b"))))]
    )

    assert re.search(
        r"\n  rung, routes to review +review rate +approved +escape rate "
        r"+injected hold +misread gate value\n",
        out,
    )
    assert re.search(r"\n  P0 +nothing +0\.000 +2 +0\.000 +0\.000 +0\.000\n", out)
    assert re.search(
        r"\n  P1 +\+ extraction or pipeline failed +0\.500 +1 +0\.000", out
    )
    assert "\n  P4 + match held on any hold type, the loop's policy " in out
    assert "P5" not in out, "the run's backend reported no confidence"
    assert "once in the escape rate and once under each cause" in out


def test_pipeline_prints_p5_at_every_edge_on_a_run_with_confidence() -> None:
    confident = approved("a", 0).model_copy(
        update={"confidence": Confidence(), "gated_confidence": (0.55,)}
    )

    out = render_pipeline([(Path("runs/a"), report(loop_run(confident)))])

    rows = [line.split() for line in out.splitlines() if line.startswith("  P5 ")]
    assert [row[4] for row in rows] == [f"0.{each}" for each in range(1, 10)]
    assert [line for line in out.splitlines() if "confidence below 0.6" in line][
        0
    ].split()[5] == "1.000"


def test_pipeline_says_when_no_labels_control_was_given() -> None:
    out = render_pipeline([(Path("runs/a"), report(loop_run(approved("a", 0))))])

    assert out.endswith(
        "Labels control\n"
        "  no labels run given; `docmatch loop --backend labels` makes it\n"
    )


def test_pipeline_prints_a_labels_run_as_the_labels_control() -> None:
    control = loop_run(approved("a", 0)).model_copy(update={"backend": labels.BACKEND})

    out = render_pipeline(
        [
            (Path("runs/a"), report(loop_run(approved("a", 0)))),
            (Path("runs/labels"), report(control)),
        ]
    )

    assert "Loop run, labels" in out
    assert "no labels run given" not in out
