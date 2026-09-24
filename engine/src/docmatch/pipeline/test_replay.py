"""Tests for loading a saved run to replay."""

import json
from pathlib import Path

import pytest

from docmatch.evals.manifest import ManifestError
from docmatch.metrics.fields import PredictionError
from docmatch.pipeline import replay


def test_a_run_that_pins_no_digests_cannot_be_replayed(saved_run: Path) -> None:
    manifest = json.loads((saved_run / "manifest.json").read_text("utf-8"))
    manifest["digests"] = {}
    (saved_run / "manifest.json").write_text(json.dumps(manifest), "utf-8")

    with pytest.raises(ManifestError, match="pins no digests"):
        replay.load(saved_run)


def test_a_run_whose_record_leaves_a_document_out_cannot_be_replayed(
    saved_run: Path,
) -> None:
    record = json.loads((saved_run / "run.json").read_text("utf-8"))
    record["documents"] = record["documents"][1:]
    (saved_run / "run.json").write_text(json.dumps(record), "utf-8")

    with pytest.raises(PredictionError, match="lists no line for eval0003"):
        replay.load(saved_run)
