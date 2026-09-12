"""The docmatch command line.

`docmatch show <document-id>` prints the labels DocILE holds for a document,
which is how a human checks that the dataset loads. `docmatch score
<document-id> --prediction <file>` scores a set of predicted header fields
against those labels, which is how a human checks the metric on a real
document before it is run over a subset.
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
    """The column the values line up in, wide enough for every fieldtype.

    It takes the names rather than the fields, because the two renderers line up
    different things: labels carry a fieldtype, scored entries carry a name.
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
"""The three things that can happen to a value, widest last for the column."""


def render_score(document_id: str, score: FieldScore) -> str:
    """The document's field score as a block a human can read in a terminal."""
    lines = [
        f"Document {document_id}",
        "",
        "Field score",
        *(
            f"  {name.ljust(len('precision'))}  {value:.3f}"
            for name, value in (
                ("precision", score.precision),
                ("recall", score.recall),
                ("F1", score.f1),
            )
        ),
        f"  {score.true_positives} matched, {score.false_negatives} missing, "
        f"{score.false_positives} spurious",
        "",
        f"Fields ({len(score.per_fieldtype)})",
    ]
    width = _width(each.fieldtype for each in score.per_fieldtype)
    for each in score.per_fieldtype:
        name = each.fieldtype.ljust(width)
        for verdict, values in zip(
            VERDICTS, (each.matched, each.missing, each.spurious), strict=True
        ):
            for value in values:
                lines.append(f"  {name}  {verdict.ljust(len(VERDICTS[-1]))}  {value}")
                name = " " * width  # the fieldtype is named once, then hangs
    return "\n".join([*lines, ""])


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
        help="a JSON object of fieldtype to a value, a list of values, or null",
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
    score = score_fields(labeled_fields(annotation), prediction.fields)
    return render_score(arguments.document_id, score)
