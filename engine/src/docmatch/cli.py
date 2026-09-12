"""The docmatch command line.

`docmatch show <document-id>` prints the labels DocILE holds for a document,
which is how a human checks that the dataset loads. `docmatch score
<document-id> --prediction <file>` scores a predicted document, its header
fields and its line items, against those labels, which is how a human checks
the metrics on a real document before they are run over a subset.
"""

import argparse
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from docmatch.docile.annotation import Annotation, FieldExtraction
from docmatch.docile.dataset import DocileDataset, DocileError
from docmatch.metrics.fields import (
    FieldScore,
    PredictionError,
    labeled_fields,
    read_prediction,
    score_fields,
)
from docmatch.metrics.line_items import (
    LineItemScore,
    labeled_line_items,
    score_line_items,
)
from docmatch.metrics.score import Score

DATA_DIR_VARIABLE = "DOCMATCH_DATA_DIR"
DEFAULT_DATA_DIR = Path("data/docile")


def resolve_data_dir(given: Path | None) -> Path:
    """Where the dataset lives: the flag, then the environment, then the default."""
    if given is not None:
        return given
    from_environment = os.environ.get(DATA_DIR_VARIABLE)
    return Path(from_environment) if from_environment else DEFAULT_DATA_DIR


def render(document_id: str, annotation: Annotation) -> str:
    """The document's labels as a block a human can read in a terminal."""
    lines = [f"Document {document_id}", "", f"KILE fields ({len(annotation.fields)})"]
    width = _width(field.fieldtype for field in annotation.fields)
    lines += _aligned(annotation.fields, indent=2, width=width)
    lines += ["", f"LIR line items ({len(annotation.line_items)})"]
    table_width = _width(cell.fieldtype for cell in annotation.cells)
    for item in annotation.line_items:
        lines.append(f"  line item {item.line_item_id}")
        lines += _aligned(item.cells, indent=4, width=table_width)
    return "\n".join([*lines, ""])


def _width(fieldtypes: Iterable[str]) -> int:
    """The column the values line up in, wide enough for every name given.

    It takes the names themselves rather than the objects holding them: both
    renderers line up a fieldtype, but each holds it on a different type, and
    the score block lines up its own labels the same way.
    """
    return max((len(fieldtype) for fieldtype in fieldtypes), default=0)


def _aligned(fields: Sequence[FieldExtraction], indent: int, width: int) -> list[str]:
    """One `fieldtype text` line per field, values in a single column.

    Addresses and descriptions carry line breaks; those continue in the value
    column rather than resetting to the left margin.
    """
    value_column = " " * (indent + width + 2)
    lines = []
    for field in fields:
        first, *rest = field.text.split("\n")
        lines.append(f"{' ' * indent}{field.fieldtype.ljust(width)}  {first}")
        lines += [f"{value_column}{line}" for line in rest]
    return lines


VERDICTS = ("matched", "missing", "spurious")
"""The three things that can happen to a value."""


def render_score(document_id: str, fields: FieldScore, rows: LineItemScore) -> str:
    """The document's two scores as a block a human can read in a terminal."""
    lines = [
        f"Document {document_id}",
        "",
        *_ratios("Field score", fields),
        "",
        f"Fields ({len(fields.per_fieldtype)})",
        *_values(fields),
        "",
        *_ratios("Line-item score", rows),
        "",
        f"Cell accuracy ({len(rows.per_fieldtype)})",
        *_accuracies(rows),
    ]
    return "\n".join([*lines, ""])


def _ratios(title: str, score: Score) -> list[str]:
    """Precision, recall, F1, and the counts they come from."""
    named = (("precision", score.precision), ("recall", score.recall), ("F1", score.f1))
    width = _width(name for name, _ in named)
    return [
        title,
        *(f"  {name.ljust(width)}  {value:.3f}" for name, value in named),
        f"  {score.true_positives} matched, {score.false_negatives} missing, "
        f"{score.false_positives} spurious",
    ]


def _values(score: FieldScore) -> list[str]:
    """Every header value and what became of it, the fieldtype named once."""
    width = _width(each.fieldtype for each in score.per_fieldtype)
    verdict_width = _width(VERDICTS)
    lines = []
    for each in score.per_fieldtype:
        name = each.fieldtype.ljust(width)
        for verdict, values in zip(
            VERDICTS, (each.matched, each.missing, each.spurious), strict=True
        ):
            for value in values:
                lines.append(f"  {name}  {verdict.ljust(verdict_width)}  {value}")
                name = " " * width  # the fieldtype is named once, then hangs
    return lines


def _accuracies(score: LineItemScore) -> list[str]:
    """Per-cell accuracy per LIR fieldtype, over the cells the labels carry.

    A fieldtype the prediction invented has no labeled cell to be accurate
    about, so its ratio column is blank and only the count is printed.
    """
    width = _width(each.fieldtype for each in score.per_fieldtype)
    blank = " " * len("0.000")
    lines = []
    for each in score.per_fieldtype:
        accuracy = f"{each.accuracy:.3f}" if each.labeled else blank
        spurious = f", {each.spurious} spurious" if each.spurious else ""
        lines.append(
            f"  {each.fieldtype.ljust(width)}  {accuracy}  "
            f"{each.correct} of {each.labeled}{spurious}"
        )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docmatch", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("document_id", help="a DocILE document id")
    common.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=(
            "the downloaded DocILE dataset "
            f"(default: ${DATA_DIR_VARIABLE}, else {DEFAULT_DATA_DIR})"
        ),
    )

    subcommands.add_parser(
        "show", parents=[common], help="print the labels for one document"
    )
    score = subcommands.add_parser(
        "score", parents=[common], help="score a prediction against those labels"
    )
    score.add_argument(
        "--prediction",
        type=Path,
        required=True,
        help='a JSON object with "fields" and "line_items"',
    )

    arguments = parser.parse_args(argv)
    dataset = DocileDataset(resolve_data_dir(arguments.data_dir))
    try:
        annotation = dataset.annotation(arguments.document_id)
        output = _run(arguments, annotation)
    except (DocileError, PredictionError) as error:
        print(f"docmatch: {error}", file=sys.stderr)
        return 1
    print(output, end="")
    return 0


def _run(arguments: argparse.Namespace, annotation: Annotation) -> str:
    if arguments.command == "show":
        return render(arguments.document_id, annotation)
    prediction = read_prediction(arguments.prediction)
    return render_score(
        arguments.document_id,
        score_fields(labeled_fields(annotation), prediction.header),
        score_line_items(labeled_line_items(annotation), prediction.rows),
    )
