"""An e-CF set: DGII's public e-CFs pinned with labels read from their own XML.

DGII's public e-CFs are licensed for local use and never redistributed, a
term that is not the owner's privacy, so they are their own regional set in
their own directory under the ignored `data/regional/`, never mixed into the
paper set. Their labels are taken from each XML (#213), which `pin` does: it
reads every `.xml` of the directory in name order with the e-CF parser
(`extraction/ecf.py`), writes the readings as `labels.csv`, and writes a `do`
manifest pinning every XML and the labels by digest, its coverage the paper
set's. Scored against those labels the parser reads 1.000 by construction, so
the number guards the parser against a change that reads a pinned e-CF
differently, and says nothing about extraction.
"""

import csv
import io
import sys
from collections.abc import Mapping
from pathlib import Path

from docmatch.evals.manifest import Manifest, write
from docmatch.evals.public import digest
from docmatch.evals.regional import FILENAME, LABELS_FILE
from docmatch.evals.run import MANIFEST_FILE
from docmatch.extraction.ecf import COVERAGE, ECFError, read

REGION = "do"
SPLIT = "ecf"
"""An e-CF set's manifest split, which `eval` reads as a set that is parsed."""
SOURCE = "DGII public e-CF: local use only, never redistributed"
"""The e-CF set's source, its licensing kept beside its digests."""


def labels_csv(labels: Mapping[str, Mapping[str, str | None]]) -> bytes:
    """`labels.csv` for an e-CF set: one row per file, one column per covered
    fieldtype, an empty cell where the e-CF has no such value. No `paper`
    column: an e-CF is printed on nothing."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow((FILENAME, *COVERAGE))
    for filename, values in labels.items():
        writer.writerow((filename, *(values.get(each) or "" for each in COVERAGE)))
    return out.getvalue().encode("utf-8")


def pin(directory: Path, *, source: str = SOURCE) -> Manifest:
    """Label every e-CF in `directory` from its own XML and pin the set.

    Writes `labels.csv` and `manifest.json` beside the XMLs. Raises `ECFError`
    naming the first file the parser cannot read, before writing anything.
    """
    paths = sorted(directory.glob("*.xml"))
    if not paths:
        raise ECFError(f"{directory} holds no .xml file")
    contents = {path.name: path.read_bytes() for path in paths}
    labels: dict[str, Mapping[str, str | None]] = {}
    for filename, content in contents.items():
        try:
            fields = read(content).fields
        except ECFError as error:
            raise ECFError(f"{directory / filename}: {error}") from error
        labels[filename] = {
            fieldtype: value if isinstance(value, str) else None
            for fieldtype, value in fields.items()
        }
    return write_set(directory, contents, labels, source=source)


def write_set(
    directory: Path,
    contents: Mapping[str, bytes],
    labels: Mapping[str, Mapping[str, str | None]],
    *,
    source: str,
) -> Manifest:
    """Write `labels.csv` and the manifest of the e-CF set whose files'
    bytes are `contents`, already in `directory`."""
    written = labels_csv(labels)
    (directory / LABELS_FILE).write_bytes(written)
    manifest = Manifest(
        split=SPLIT,
        seed=0,
        source=source,
        size=len(contents),
        document_ids=tuple(contents),
        digests={name: digest(content) for name, content in contents.items()},
        region=REGION,
        coverage=COVERAGE,
        files={LABELS_FILE: digest(written)},
    )
    write(manifest, directory / MANIFEST_FILE)
    return manifest


def main(arguments: list[str]) -> int:
    """`python -m docmatch.evals.ecf <directory>`: pin an e-CF set."""
    if len(arguments) != 1:
        print("usage: python -m docmatch.evals.ecf <directory of e-CF XMLs>")
        return 2
    try:
        manifest = pin(Path(arguments[0]))
    except ECFError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"pinned {manifest.size} e-CFs in {arguments[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
