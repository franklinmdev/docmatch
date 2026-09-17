"""The docmatch command line.

`docmatch show <document-id>` prints the labels DocILE holds for a document,
which is how a human checks that the dataset loads. `docmatch score
<document-id> --prediction <file>` scores a predicted document, its header
fields and its line items, against those labels, which is how a human checks
the metrics on a real document before they are run over a subset.

`docmatch subset` re-derives the committed fixed subset from the split it was
drawn from, offline, which is how a reader checks that the pinned list is still
the one the seed and the rejects draw. `docmatch download` fetches the pinned
public copies and verifies each against its digest, which is what a fresh
machine runs before a benchmark row. `docmatch eval --predictions <file>`
scores a whole run over that subset, which is the command every number in the
README comes from.

`docmatch corpus` recomputes the counts the rules in `metrics` are justified
by, which is how a reader checks the numbers the docstrings there assert.

`docmatch extract` reads the pinned public copies with one backend and writes
the predictions file the eval scores, which is the half of a benchmark row that
costs money.

Rendering lives here rather than beside each metric: the numbers are the
engine's, the terminal is this module's.
"""

import argparse
import os
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import get_args

from docmatch.docile.annotation import Annotation, FieldExtraction
from docmatch.docile.dataset import DatasetNotFoundError, DocileDataset, DocileError
from docmatch.evals import corpus, manifest, public
from docmatch.evals.confidence import Buckets, Calibration, Sweep
from docmatch.evals.corpus import Census, Coverage, RuleCoverage, Survey
from docmatch.evals.manifest import Manifest, ManifestError, Reason
from docmatch.evals.public import PublicCopyError
from docmatch.evals.run import (
    Ablation,
    DerivedTotals,
    FieldTypeTotals,
    GateTotals,
    SubsetScore,
    read_confidence,
    read_currency_symbols,
    read_predictions,
    score_subset,
)
from docmatch.extraction import azure, backends, gemini, pages
from docmatch.extraction.extractor import ExtractionError
from docmatch.extraction.run import (
    ATTEMPTS,
    COST_CAP,
    DocumentRun,
    Run,
    extract_subset,
    write_confidence,
    write_currency_symbols,
    write_manifest,
    write_predictions,
    write_record,
)
from docmatch.metrics.fields import (
    FieldScore,
    PredictionError,
    labeled_fields,
    read_prediction,
    score_fields,
)
from docmatch.metrics.line_items import (
    CellAccuracy,
    LineItemScore,
    labeled_line_items,
    score_line_items,
)
from docmatch.metrics.score import Score, ratio

DATA_DIR_VARIABLE = "DOCMATCH_DATA_DIR"
DEFAULT_DATA_DIR = Path("data/docile")
DEFAULT_COPIES_DIR = Path("data/ucsf")


def money(given: str) -> Decimal:
    """A dollar amount from the command line, or an error argparse can print.

    `Decimal` raises `InvalidOperation`, an `ArithmeticError`, which argparse
    does not turn into a message, so `--cost-cap 0,05` would end in a traceback
    rather than in "invalid money value". It also accepts `NaN`, `Infinity`
    and negatives, none of which is a cap: a NaN raises at its first
    comparison, after the first document has been paid for, an infinity turns
    the cap off, and a cap of nothing or less refuses every retry, the free
    ones included.
    """
    try:
        amount = Decimal(given)
    except InvalidOperation:
        raise ValueError(given) from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError(given)
    return amount


def positive(given: str) -> int:
    """A count from the command line that has to be at least one.

    `--attempts 0` would otherwise fail every document with "no attempt was
    made" and `--long-edge 0` would fail every one of them in the renderer,
    reporting a mistyped flag as a hundred broken documents.
    """
    number = int(given)
    if number < 1:
        raise ValueError(given)
    return number


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
        *_accuracies(rows.per_fieldtype),
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


def _accuracies(accuracies: Sequence[CellAccuracy]) -> list[str]:
    """Per-cell accuracy per LIR fieldtype, over the cells the labels carry.

    A fieldtype the prediction invented has no labeled cell to be accurate
    about, so its ratio column is blank and only the count is printed.
    """
    width = _width(each.fieldtype for each in accuracies)
    blank = " " * len("0.000")
    lines = []
    for each in accuracies:
        accuracy = f"{each.accuracy:.3f}" if each.labeled else blank
        spurious = f", {each.spurious} spurious" if each.spurious else ""
        lines.append(
            f"  {each.fieldtype.ljust(width)}  {accuracy}  "
            f"{each.correct} of {each.labeled}{spurious}"
        )
    return lines


def render_subset(path: Path, pinned: Manifest, note: tuple[str, str]) -> str:
    """The pinned subset, and what just happened to it."""
    return "\n".join(
        [
            "Fixed subset",
            *_rows(
                ("manifest", str(path)),
                ("split", pinned.split),
                ("source", pinned.source),
                ("seed", str(pinned.seed)),
                ("size", str(pinned.size)),
                ("rejected", _rejects(pinned.rejected)),
                note,
            ),
            "",
        ]
    )


REASONS: tuple[Reason, ...] = get_args(Reason)


def _rejects(rejected: dict[str, Reason]) -> str:
    """How many documents admission left out, and why, in one line."""
    if not rejected:
        return "0"
    counts = Counter(rejected.values())
    why = ", ".join(f"{counts[each]} {each}" for each in REASONS if counts[each])
    return f"{len(rejected)}: {why}"


def render_download(path: Path, copies: Path, downloaded: public.Downloaded) -> str:
    """Which pinned copies were fetched and which were already in place."""
    return "\n".join(
        [
            "Public copies, each verified against its digest",
            *_rows(
                ("manifest", str(path)),
                ("copies", str(copies)),
                ("fetched", str(len(downloaded.fetched))),
                ("kept", str(len(downloaded.kept))),
            ),
            "",
        ]
    )


def _rows(*rows: tuple[str, str]) -> list[str]:
    """Labeled values in one column, the way every block here lines them up."""
    width = _width(label for label, _ in rows)
    return [f"  {label.ljust(width)}  {value}" for label, value in rows]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docmatch", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    dataset = argparse.ArgumentParser(add_help=False)
    dataset.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=(
            "the downloaded DocILE dataset "
            f"(default: ${DATA_DIR_VARIABLE}, else {DEFAULT_DATA_DIR})"
        ),
    )
    document = argparse.ArgumentParser(add_help=False)
    document.add_argument("document_id", help="a DocILE document id")

    subcommands.add_parser(
        "show",
        parents=[document, dataset],
        help="print the labels for one document",
    )
    score = subcommands.add_parser(
        "score",
        parents=[document, dataset],
        help="score a prediction against those labels",
    )
    score.add_argument(
        "--prediction",
        type=Path,
        required=True,
        help='a JSON object with "fields" and "line_items"',
    )
    evaluate = subcommands.add_parser(
        "eval",
        parents=[dataset],
        help="score a run of predictions over the fixed subset",
    )
    evaluate.add_argument(
        "--predictions",
        type=Path,
        required=True,
        help=(
            "a JSON object keyed by document id, each holding one prediction; a "
            f"{CURRENCY_SYMBOLS_FILE} beside it is read too"
        ),
    )
    evaluate.add_argument(
        "--manifest",
        type=Path,
        default=manifest.MANIFEST,
        help=f"the subset to score over (default: {manifest.MANIFEST.name})",
    )
    counts = subcommands.add_parser(
        "corpus",
        parents=[dataset],
        help="recompute the counts the rules in metrics are justified by",
    )
    counts.add_argument(
        "--split",
        default=corpus.SPLIT,
        help=f"the split to count over (default: {corpus.SPLIT})",
    )
    extract = subcommands.add_parser(
        "extract",
        parents=[dataset],
        help="read the fixed subset with one backend and write its predictions",
    )
    extract.add_argument(
        "--out",
        type=Path,
        required=True,
        help="a directory to write predictions.json, manifest.json and run.json into",
    )
    extract.add_argument(
        "--backend",
        choices=backends.BACKENDS,
        default=backends.BACKENDS[0],
        help=f"the vendor to read with (default: {backends.BACKENDS[0]})",
    )
    extract.add_argument(
        "--model",
        default=None,
        help=(
            "the model to read with, one the backend has a price for (default: "
            f"the backend's own, {gemini.MODEL} for gemini, {azure.MODEL} for azure)"
        ),
    )
    extract.add_argument(
        "--manifest",
        type=Path,
        default=manifest.MANIFEST,
        help=f"the subset to read (default: {manifest.MANIFEST.name})",
    )
    extract.add_argument(
        "--copies",
        type=Path,
        default=DEFAULT_COPIES_DIR,
        help=(
            "where the pinned public copies are kept, each verified before the "
            f"run starts (default: {DEFAULT_COPIES_DIR})"
        ),
    )
    extract.add_argument(
        "--long-edge",
        type=positive,
        default=pages.LONG_EDGE,
        help=(
            "pixels on a rendered page's longer side, for a backend that renders "
            f"pages (default: {pages.LONG_EDGE})"
        ),
    )
    extract.add_argument(
        "--attempts",
        type=positive,
        default=ATTEMPTS,
        help=f"tries per document before giving up (default: {ATTEMPTS})",
    )
    extract.add_argument(
        "--cost-cap",
        type=money,
        default=COST_CAP,
        help=(
            "US dollars one document may have spent before it is given up on "
            f"(default: {COST_CAP})"
        ),
    )
    extract.add_argument(
        "--limit",
        type=positive,
        default=None,
        help=(
            "read only the first N pinned documents, for a cheap check that the "
            "run works; a benchmark row needs the whole subset"
        ),
    )
    subset = subcommands.add_parser(
        "subset",
        parents=[dataset],
        help="check, offline, that the seed and rejects still draw the pinned subset",
    )
    subset.add_argument(
        "--manifest",
        type=Path,
        default=manifest.MANIFEST,
        help=f"the pinned subset (default: {manifest.MANIFEST.name} beside the code)",
    )
    subset.add_argument(
        "--seed",
        type=int,
        default=None,
        help=f"draw with this seed, only with --write (default: {manifest.SEED})",
    )
    subset.add_argument(
        "--size",
        type=int,
        default=None,
        help=(
            "draw this many documents, only with --write, and only for a "
            f"fixture: the benchmark subset is {manifest.SIZE} documents"
        ),
    )
    subset.add_argument(
        "--write",
        action="store_true",
        help=(
            "fetch public copies in ranking order, admit them, and write the "
            "manifest, replacing any there"
        ),
    )
    download = subcommands.add_parser(
        "download",
        parents=[dataset],
        help="download the pinned public copies and verify each against its digest",
    )
    download.add_argument(
        "--manifest",
        type=Path,
        default=manifest.MANIFEST,
        help=f"the pinned subset (default: {manifest.MANIFEST.name} beside the code)",
    )
    download.add_argument(
        "--copies",
        type=Path,
        default=DEFAULT_COPIES_DIR,
        help=f"where public copies are kept (default: {DEFAULT_COPIES_DIR})",
    )

    arguments = parser.parse_args(argv)
    if arguments.command == "subset" and not arguments.write:
        drawing = [
            flag
            for flag, given in (("--seed", arguments.seed), ("--size", arguments.size))
            if given is not None
        ]
        if drawing:
            # Silently checking the pinned seed while the caller asked about
            # another one would be a wrong answer, not a missing feature.
            subset.error(f"{' and '.join(drawing)} draws a subset, so it needs --write")
    try:
        output, exit_code = _run(arguments)
    except (
        DocileError,
        PredictionError,
        ManifestError,
        ExtractionError,
        PublicCopyError,
    ) as error:
        print(f"docmatch: {error}", file=sys.stderr)
        return 1
    print(output, end="")
    return exit_code


def _run(arguments: argparse.Namespace) -> tuple[str, int]:
    """The report the command asked for, and what to exit with.

    A command reports a number even when the answer is bad news, so the exit
    code travels beside the report rather than in an exception.
    """
    dataset = DocileDataset(resolve_data_dir(arguments.data_dir))
    if arguments.command == "subset":
        return _subset(arguments, dataset)
    if arguments.command == "download":
        pinned = manifest.load(arguments.manifest)
        downloaded = public.download(dataset, pinned, arguments.copies, public.fetch)
        return render_download(arguments.manifest, arguments.copies, downloaded), 0
    if arguments.command == "corpus":
        return _corpus(arguments, dataset)
    if arguments.command == "extract":
        return _extract(arguments, dataset)
    if arguments.command == "eval":
        run = score_subset(
            dataset,
            manifest.load(arguments.manifest),
            read_predictions(arguments.predictions),
            currency_symbols=read_currency_symbols(
                arguments.predictions.parent / CURRENCY_SYMBOLS_FILE
            ),
            confidence=read_confidence(arguments.predictions.parent / CONFIDENCE_FILE),
        )
        return render_eval(arguments.manifest, run), 0
    annotation = dataset.annotation(arguments.document_id)
    if arguments.command == "show":
        return render(arguments.document_id, annotation), 0
    prediction = read_prediction(arguments.prediction)
    return (
        render_score(
            arguments.document_id,
            score_fields(labeled_fields(annotation), prediction.header),
            score_line_items(labeled_line_items(annotation), prediction.rows),
        ),
        0,
    )


def _subset(arguments: argparse.Namespace, dataset: DocileDataset) -> tuple[str, int]:
    """Admit and write the pinned subset, or check that the seed still draws it.

    Writing is the one step that asks the archive for anything; the check reads
    DocILE's metadata and the manifest's rejects, and no network. Not
    reproducing is a failure and exits non-zero, but the report is still
    printed: what the seed draws now is the thing worth looking at.
    """
    if arguments.write:
        written = manifest.admitted(
            _pool(dataset, manifest.SPLIT, manifest.SOURCE),
            split=manifest.SPLIT,
            seed=manifest.SEED if arguments.seed is None else arguments.seed,
            source=manifest.SOURCE,
            size=manifest.SIZE if arguments.size is None else arguments.size,
            admit=public.admitter(dataset, public.fetch),
        )
        manifest.write(written, arguments.manifest)
        return render_subset(arguments.manifest, written, ("written", "yes")), 0

    pinned = manifest.load(arguments.manifest)
    drawn = pinned.reproduced_from(_pool(dataset, pinned.split, pinned.source))
    note = (
        "reproduced",
        "yes" if drawn == pinned.document_ids else _drift(pinned, drawn),
    )
    return (
        render_subset(arguments.manifest, pinned, note),
        0 if note[1] == "yes" else 1,
    )


def _pool(dataset: DocileDataset, split: str, source: str) -> tuple[str, ...]:
    """The split's documents whose DocILE source is `source`, the ranked pool."""
    return tuple(
        document_id
        for document_id in dataset.document_ids(split)
        if dataset.annotation(document_id).metadata.source == source
    )


def _extract(arguments: argparse.Namespace, dataset: DocileDataset) -> tuple[str, int]:
    """Read the subset with one backend, write the run, report what it cost.

    A run that produced nothing at all exits non-zero. Anything less is
    reported and left to the reader: some documents fail, and the score of the
    ones that did not is still the thing worth looking at.

    Documents are kept as they come. Whatever stops the run before the last
    one, a Ctrl-C or a failure nothing below expected, the documents already
    paid for are written with a manifest covering exactly them, so their score
    is over what was read and their cost is on record. That is what
    `extract_subset` yields for, and it would be lost if the run were gathered
    up before the first write.
    """
    pinned = manifest.load(arguments.manifest)
    if arguments.limit is not None:
        pinned = pinned.first(arguments.limit)
    # The key, the price and the public copies before the directory, so a run
    # that cannot start leaves nothing behind; the directory before the first
    # document, because an --out that cannot be written is worth a hundred
    # documents of spend.
    backend = backends.extractor(
        arguments.backend, arguments.model, long_edge=arguments.long_edge
    )
    reading = extract_subset(
        backend,
        dataset,
        pinned,
        arguments.copies,
        attempts=arguments.attempts,
        cost_cap=arguments.cost_cap,
    )
    _prepare(arguments.out)

    def finished(covered: Manifest, documents: Sequence[DocumentRun]) -> Run:
        return Run(
            backend=arguments.backend,
            requested_model=backend.model,
            manifest=covered,
            long_edge=(
                arguments.long_edge if arguments.backend in backends.RENDERING else None
            ),
            documents=tuple(documents),
        )

    documents: list[DocumentRun] = []
    try:
        for document in reading:
            documents.append(document)
    except BaseException as stopped:
        if documents:
            _write(finished(pinned.first(len(documents)), documents), arguments.out)
        if isinstance(stopped, KeyboardInterrupt):
            kept = f"; what was read is in {arguments.out}" if documents else ""
            raise ExtractionError(
                f"interrupted after {len(documents)} of {pinned.size} documents{kept}"
            ) from None
        raise
    extracted = finished(pinned, documents)
    _write(extracted, arguments.out)
    return (
        render_extract(arguments.out, extracted),
        0 if extracted.predicted else 1,
    )


CURRENCY_SYMBOLS_FILE = "currency_symbols.json"
"""Read by `docmatch eval` beside the predictions when a run saved one."""

CONFIDENCE_FILE = "confidence.json"
"""Read by `docmatch eval` beside the predictions; empty or absent is no signal."""

RUN_FILES = (
    "predictions.json",
    "manifest.json",
    "run.json",
    CONFIDENCE_FILE,
    CURRENCY_SYMBOLS_FILE,
)


def _prepare(out: Path) -> None:
    """Make sure the run can be written, before the first document is paid for.

    The directory is made now, and the run's file names checked for a directory in
    the way of one of them: a write that fails after the run has ended costs
    the whole run, and one that fails on the second file leaves predictions
    beside no manifest, to be scored against the whole subset.
    """
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ExtractionError(f"cannot write the run to {out}: {error}") from error
    for name in RUN_FILES:
        if (out / name).is_dir():
            raise ExtractionError(
                f"cannot write the run to {out}: {name} is a directory there"
            )
    if not os.access(out, os.W_OK):
        raise ExtractionError(f"cannot write the run to {out}: not writable")


def _write(extracted: Run, out: Path) -> None:
    """The files of a run, or a message rather than a traceback."""
    try:
        write_predictions(extracted, out / RUN_FILES[0])
        write_manifest(extracted, out / RUN_FILES[1])
        write_record(extracted, out / RUN_FILES[2])
        write_confidence(extracted, out / RUN_FILES[3])
        write_currency_symbols(extracted, out / RUN_FILES[4])
    except OSError as error:
        raise ExtractionError(f"cannot write the run to {out}: {error}") from error


def _corpus(arguments: argparse.Namespace, dataset: DocileDataset) -> tuple[str, int]:
    """Recompute the counts, or say that there is nothing here to count.

    A dataset that was never downloaded is the one error this command does not
    report as one. DocILE is not committed and CI never has it, so a survey of
    it is skipped rather than failed, the way a test is skipped when what it
    needs is absent.
    """
    try:
        document_ids = dataset.document_ids(arguments.split)
    except DatasetNotFoundError:
        return f"Corpus\n  skipped, no dataset at {dataset.root}\n", 0
    return (
        render_corpus(
            dataset.root,
            corpus.survey(
                (dataset.annotation(document_id) for document_id in document_ids),
                arguments.split,
            ),
        ),
        0,
    )


def render_corpus(root: Path, surveyed: Survey) -> str:
    """Every count the survey took, under the module whose docstring asserts it.

    Grouped that way so the report and the docstrings can be read side by side,
    which is the whole point of taking the counts again.
    """
    annotated = surveyed.corpus
    dates, repeats = surveyed.dates, surveyed.repeats
    lines = [
        f"Corpus  {root}, {surveyed.split}",
        *_counted(
            ("documents", annotated.documents),
            ("KILE labels", annotated.fields),
            ("LIR cells", annotated.cells),
            ("line items", annotated.rows),
            ("documents with no table", annotated.without_a_table),
            ("largest table", annotated.largest_table),
        ),
        "",
        "Rules read, of the header labels",
        *_coverage(surveyed.header_rules),
        "",
        "Rules read, of the line-item cells",
        *_coverage(surveyed.cell_rules),
        "",
        f"Fieldtypes ({len(surveyed.coverage)})",
        *_fieldtypes(surveyed.coverage),
        "",
        "metrics/normalization.py",
        *_counted(
            ("numeric dates proving month first", dates.month_first),
            ("numeric dates proving day first", dates.day_first),
            ("dates the month rule's strictness turns on", dates.three_letter_months),
        ),
        *_census("numbers written with a lone dot and three digits", surveyed.lone_dot),
        "",
        "metrics/fields.py",
        *_counted(
            ("header fieldtypes labeled more than once", repeats.fieldtypes),
            ("  of those, one value written twice", repeats.one_value),
            ("labels repeating a line inside one box", repeats.within_a_label),
        ),
        "",
        "metrics/line_items.py",
        *_counted(
            ("rows repeating a fieldtype", repeats.rows),
            ("  of those, still after normalizing", repeats.rows_after_normalization),
        ),
    ]
    return "\n".join([*lines, ""])


def _counted(*rows: tuple[str, int]) -> list[str]:
    """Labeled counts, the labels in one column and the numbers in another."""
    width = _width(label for label, _ in rows)
    figures = _figures(count for _, count in rows)
    return [
        f"  {label.ljust(width)}  {str(count).rjust(figures)}" for label, count in rows
    ]


def _figures(counts: Iterable[int]) -> int:
    """The column the counts line up in, wide enough for every one given.

    The other half of `_width`: a name is padded on the right and a count on
    the left, and every block here lines up some of both.
    """
    return max((len(str(count)) for count in counts), default=0)


def _census(title: str, counted: Census) -> list[str]:
    """How a shape is spread over the fieldtypes carrying it, and how many in all.

    The total is what the rule it justifies was weighed against; the fieldtypes
    are what says which way the weighing went, because what a value means is
    what its fieldtype says it means.
    """
    total = sum(count for _, count in counted)
    return [
        f"  {title} ({total})",
        *(f"  {line}" for line in _counted(*counted)),
    ]


def _coverage(rules: Sequence[RuleCoverage]) -> list[str]:
    """What share of everything a rule is given it reads, and of how many."""
    width = _width(each.rule for each in rules)
    read = _figures(each.read for each in rules)
    return [
        f"  {each.rule.ljust(width)}  {each.share:6.1%}  "
        f"{str(each.read).rjust(read)} of {each.labels}"
        for each in rules
    ]


def _fieldtypes(coverage: Sequence[Coverage]) -> list[str]:
    """Every fieldtype, its rule, how many labels it has, and how many are read.

    A text fieldtype has no share to report, because the text rule reads
    everything; what is worth reporting there is how much of it the number rule
    would read, which is why three of them are text at all.
    """
    width = _width(each.fieldtype for each in coverage)
    rules = _width(each.rule for each in coverage)
    labels = _figures(each.labels for each in coverage)
    lines = []
    for each in coverage:
        read = " " * 6 if each.rule == "text" else f"{each.share:6.1%}"
        note = f"  {each.number_share:6.1%} as a number" if each.rule == "text" else ""
        lines.append(
            f"  {each.fieldtype.ljust(width)}  {each.rule.ljust(rules)}  "
            f"{str(each.labels).rjust(labels)}  {read}{note}".rstrip()
        )
    return lines


def render_extract(where: Path, extracted: Run) -> str:
    """What a run cost and how much of it worked, as a block a human can paste.

    Counts, money and seconds. No label text and no model text: rule 6 applies
    to a run report exactly as it applies to an eval report, and what a reader
    needs from this block is whether the run is sound enough for its score to
    mean anything.
    """
    tokens = extracted.tokens
    lines = [
        "Extraction run",
        *_rows(
            ("backend", extracted.backend),
            ("requested", extracted.requested_model),
            ("served", _served(extracted.documents)),
            ("split", extracted.manifest.split),
            ("size", str(extracted.manifest.size)),
            (
                "long edge",
                "not rendered"
                if extracted.long_edge is None
                else f"{extracted.long_edge} px",
            ),
            ("predicted", str(len(extracted.predicted))),
            ("failed", str(len(extracted.failed))),
            ("written to", str(where)),
        ),
        "",
        "Cost",
        *_rows(
            ("total", f"${extracted.cost:.4f}"),
            ("per document", f"${extracted.cost_per_document:.6f}"),
            ("input tokens", f"{tokens.input_tokens:,}"),
            ("cached input", f"{tokens.cached_input_tokens:,}"),
            ("cache writes", f"{tokens.cache_write_tokens:,}"),
            ("output tokens", f"{tokens.output_tokens:,}"),
            ("pages billed", f"{tokens.pages:,}"),
        ),
        "",
        "Latency, over the documents that produced a prediction",
        *_rows(
            ("p50", f"{extracted.latency(50):.2f} s"),
            ("p95", f"{extracted.latency(95):.2f} s"),
        ),
        *_failures(extracted.failed),
    ]
    return "\n".join([*lines, ""])


def _served(documents: Sequence[DocumentRun]) -> str:
    """Every served model the vendor reported, in the order they first appeared."""
    served = dict.fromkeys(
        each.served_model for each in documents if each.served_model is not None
    )
    return ", ".join(served) or "none reported"


def _failures(failed: Sequence[DocumentRun]) -> list[str]:
    """Every document that produced nothing, and what stopped it.

    Listed rather than counted, because a run that failed a fifth of the subset
    is a broken run and only the reasons say which kind of broken. The same
    documents come back out of `docmatch eval` as its "not predicted" list.
    """
    if not failed:
        return []
    width = _width(each.document_id for each in failed)
    return [
        "",
        f"Failed ({len(failed)})",
        *(
            f"  {each.document_id.ljust(width)}  after {each.attempts}, {each.failure}"
            for each in failed
        ),
    ]


def render_eval(path: Path, run: SubsetScore) -> str:
    """A whole run over the fixed subset as a block a human can paste anywhere.

    Counts and ratios only: `run` explains why no label text appears here.
    """
    predicted = len(run.documents) - len(run.not_predicted)
    lines = [
        "Fixed subset",
        *_rows(
            ("manifest", str(path)),
            ("split", run.manifest.split),
            ("size", str(run.manifest.size)),
            ("predicted", str(predicted)),
            ("not predicted", str(len(run.not_predicted))),
            ("not pinned", str(len(run.unpinned))),
        ),
        "",
        *_listed("Not predicted", run.not_predicted),
        *_listed("Not pinned", run.unpinned),
        *_ratios("Field score", run.fields),
        "",
        f"Fields ({len(run.per_fieldtype)})",
        *_totals(run.per_fieldtype),
        "",
        f"Derived values ({len(run.derived)})",
        *_derived(run.derived),
        "",
        *_ratios("Line-item score", run.line_items),
        "",
        f"Cell accuracy ({len(run.per_cell_fieldtype)})",
        *_accuracies(run.per_cell_fieldtype),
        "",
        *_calibration(run.calibration),
        "",
        *_gate(run.gate),
        "",
        *_ablation(run.ablation, run.gate.checked, run.sweep),
    ]
    return "\n".join([*lines, ""])


def _listed(title: str, document_ids: Sequence[str]) -> list[str]:
    """The documents behind a count, in manifest then predictions-file order.

    A count alone cannot be acted on, and a run that left out a third of the
    subset is a broken run rather than a low score.
    """
    if not document_ids:
        return []
    return [
        f"{title} ({len(document_ids)})",
        *(f"  {each}" for each in document_ids),
        "",
    ]


def _totals(totals: Sequence[FieldTypeTotals]) -> list[str]:
    """Every KILE fieldtype and how it went, in the words `_ratios` uses.

    F1 leads because it is the column being compared; precision and recall
    follow from the counts beside it.
    """
    width = _width(each.fieldtype for each in totals)
    return [f"  {each.fieldtype.ljust(width)}  {_counts(each)}" for each in totals]


def _counts(totals: FieldTypeTotals) -> str:
    """One fieldtype's F1 and the counts it comes from."""
    spurious = f", {totals.spurious} spurious" if totals.spurious else ""
    return (
        f"{totals.f1:.3f}  {totals.matched} matched, {totals.missing} missing{spurious}"
    )


DERIVED_VIEWS = ("as read", "with derived")
"""The two scores of a fieldtype code adds values to."""


def _derived(derived: Sequence[DerivedTotals]) -> list[str]:
    """Each fieldtype code adds to, as the backend read it and with what code added.

    Side by side, so a gain on the line below is credited to code and not to
    the backend. The field score above already counts the derived values.
    """
    width = _width(each.fieldtype for each in derived)
    views = _width(DERIVED_VIEWS)
    lines = []
    for each in derived:
        name = each.fieldtype.ljust(width)
        for view, totals in zip(
            DERIVED_VIEWS, (each.as_read, each.with_derived), strict=True
        ):
            lines.append(f"  {name}  {view.ljust(views)}  {_counts(totals)}")
            name = " " * width  # the fieldtype is named once, then hangs
    return lines


def _gate(totals: GateTotals) -> list[str]:
    """Verdict counts, the pass rate with n checked, and unreadable values per rule.

    n checked sits beside the rate because a rate over three readings and one
    over ninety are not the same finding.
    """
    return [
        "Gate",
        *_rows(
            ("passed", str(totals.passed)),
            ("failed", str(totals.failed)),
            ("not checked", str(totals.not_checked)),
            ("pass rate", f"{totals.pass_rate:.3f} of {totals.checked} checked"),
        ),
        "",
        "Unreadable",
        *_rows(*((rule, str(count)) for rule, count in totals.unreadable.items())),
    ]


NO_SIGNAL = "no signal"
"""What every confidence section says for a run carrying none."""


def _calibration(calibration: Calibration | None) -> list[str]:
    """Accuracy by confidence bucket, header values and cells apart."""
    if calibration is None:
        return ["Calibration", f"  {NO_SIGNAL}"]
    return [
        "Calibration, header values",
        *_buckets(calibration.header),
        "",
        "Calibration, line-item cells",
        *_buckets(calibration.cells),
        "",
        # A value the backend never read has no confidence to be bucketed by.
        "Calibration speaks to precision only.",
    ]


def _buckets(buckets: Buckets) -> list[str]:
    """Ten fixed buckets and the no-confidence line, accuracy left out at n 0."""
    labeled = [
        (f"[{each.low:.1f}, {each.high:.1f}{']' if each.high == 1.0 else ')'}", each)
        for each in buckets.buckets
        if each.low is not None and each.high is not None
    ]
    labeled.append(("no confidence", buckets.no_confidence))
    return _table(
        ("confidence", "n", "accuracy"),
        [
            (label, str(each.n), f"{ratio(each.correct, each.n):.3f}")
            if each.n
            else (label, str(each.n))
            for label, each in labeled
        ],
    )


def _ablation(ablation: Ablation, checked: int, swept: Sweep | None) -> list[str]:
    """What the gate caught among checked readings, beside what confidence did."""
    lines = [
        f"Gate ablation, over {checked} checked",
        *_rows(
            ("catches", str(ablation.catches)),
            ("misses", str(ablation.misses)),
            ("false alarms", str(ablation.false_alarms)),
            *((("confidence", NO_SIGNAL),) if swept is None else ()),
        ),
    ]
    if swept is None:
        return lines
    return [
        *lines,
        "",
        f"Confidence sweep, over {checked} checked",
        *_table(
            (
                "edge",
                "gate only",
                "confidence only",
                "both",
                "neither",
                "gate false alarms",
                "confidence false alarms",
            ),
            [
                (
                    f"{line.edge:.1f}",
                    *map(
                        str,
                        (
                            line.gate_only,
                            line.confidence_only,
                            line.both,
                            line.neither,
                            line.gate_false_alarms,
                            line.confidence_false_alarms,
                        ),
                    ),
                )
                for line in swept.lines
            ],
        ),
        "",
        *_rows(
            ("readings with a gated value with no confidence", str(swept.unconfident))
        ),
    ]


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """A header over rows: the first column left-aligned, the rest right-aligned.

    A row may stop short, which leaves its last columns blank.
    """
    widths = [
        max(
            len(header[column]),
            *(len(row[column]) for row in rows if column < len(row)),
        )
        for column in range(len(header))
    ]

    def aligned(cells: Sequence[str]) -> str:
        first, *rest = cells
        return "  ".join(
            [
                f"  {first.ljust(widths[0])}",
                *(
                    cell.rjust(width)
                    for cell, width in zip(rest, widths[1:], strict=False)
                ),
            ]
        )

    return [aligned(header), *(aligned(row) for row in rows)]


def _drift(pinned: Manifest, drawn: Sequence[str]) -> str:
    """What the seed draws now that the manifest does not pin.

    Order counts as drift, not only membership: a prefix of the subset is
    itself a sample, so the order the seed draws in is part of what is pinned.
    """
    documents = set(pinned.document_ids)
    unpinned = [document_id for document_id in drawn if document_id not in documents]
    if not unpinned:
        return "no, the same documents in a different order"
    return f"no, {len(unpinned)} of the {pinned.size} documents are not pinned"
