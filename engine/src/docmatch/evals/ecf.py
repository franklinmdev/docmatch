"""The e-CF parser backend: an e-CF's XML read into a `Prediction`, at $0.

An e-CF is DGII's electronic comprobante, an XML document whose values are
already structured, so it is parsed and never sent to a model (#214, #222).
`--backend ecf` is this parser behind the `Extractor` seam: no vendor call,
no usage, no cost and no served model. Its F1 measures parsing, near 1.000
by construction, never extraction.

The mapping follows DGII's XSDs v1.0 of 16/10/2025 for the four types read
here, E31, E32, E41 and E43 (root `ECF`, no namespace, read 2026-10-23), and
the "Formato Comprobante Fiscal Electrónico (e-CF) V1.0" of October 2025
(read 2026-10-23):

- `document_id` is `IdDoc/eNCF`, `date_issue` is `Emisor/FechaEmision`,
  which the XSDs write `dd-MM-yyyy` and is given here as `YYYY-MM-DD` so the
  date rule reads it without guessing the order.
- The parties: the seller is the vendor and the other party the customer. On
  an E31 and an E32 the issuer sells, so `Emisor` is the vendor and
  `Comprador` the customer. An E41 compras and an E43 gastos menores are
  issued by the one who buys, so there `Emisor` is the customer and an E41's
  `Comprador` the seller; an E43 names no seller. That reading of the two
  types is not stated in the XSDs or the format, and is unverified.
- `amount_total_net` is `MontoGravadoTotal` plus `MontoExento`, what the
  format says the total is before ITBIS and additional taxes;
  `amount_total_tax` is `TotalITBIS` and `amount_total_gross` `MontoTotal`.
- Additional taxes, by the format's Table I: an ISC amount (`Especifico` or
  `Advalorem`, codes 006 to 039) is `amount_tax_selective`; the
  `OtrosImpuestosAdicionales` of code 001, the legal tip, is `amount_tip`,
  and of any other code `amount_tax_other`. Each is summed over the header's
  `ImpuestosAdicionales`.
- `document_id_modified` is `InformacionReferencia/NCFModificado`.
- `amount_retention_itbis` and `amount_retention_isr` are the header's
  `TotalITBISRetenido` and `TotalISRRetencion` when printed, otherwise the
  sum of the lines' `Retencion` amounts, which the format says the header
  totals are.

A sum is of the values present and absent when none is. The header is all
the parser reports: no line item is read, because whether a line's
`PrecioUnitarioItem` is net or gross of ITBIS is unverified (#206), and the
scorer covers no line-item fieldtype on a regional set. The XMLDSig
signature is not verified (#222).

The XML is parsed with the standard library's `xml.etree.ElementTree`
(Python 3.12 documentation, its "XML vulnerabilities" section read
2026-10-23): it expands no external entity and retrieves no DTD, and the
expat it binds, 2.8.5 here, is past 2.4.1, the version that stops the
billion laughs and quadratic blowup. An e-CF is a file of the owner's set,
pinned by digest before it is read, not a stranger's upload.

The e-CF set
------------

DGII's public e-CFs are licensed for local use and never redistributed, a
term that is not the owner's privacy, so they are their own regional set in
their own directory under the ignored `data/regional/`, never mixed into the
paper set. Their labels are taken from each XML (#213), which `pin` does: it
reads every `.xml` of the directory in name order with this parser, writes
the readings as `labels.csv`, and writes a `do` manifest pinning every XML
and the labels by digest, its coverage the paper set's. Scored against those
labels the parser reads 1.000 by construction, so the number guards the
parser against a change that reads a pinned e-CF differently, and says
nothing about extraction.
"""

import csv
import io
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from docmatch.evals.manifest import Manifest, write
from docmatch.evals.public import digest
from docmatch.evals.regional import FILENAME, LABELS_FILE
from docmatch.evals.run import MANIFEST_FILE
from docmatch.extraction.extractor import (
    NOTHING,
    Document,
    Extraction,
    ExtractionError,
)
from docmatch.metrics.fields import Prediction

BACKEND = "ecf"
"""The parser's name as `--backend` gives it."""
MODEL = "ecf-parser-v1.0"
"""What a run of the parser records as its requested model: the XSD version it
reads, since it has no model."""
TYPES = frozenset({"31", "32", "41", "43"})
"""The e-CF types the parser reads."""
BOUGHT = frozenset({"41", "43"})
"""The types the buyer issues, so the issuer is the customer (unverified)."""
TIP = "001"
"""Table I's code for the legal tip."""
REGION = "do"
SPLIT = "ecf"
"""An e-CF set's manifest split, which `eval` reads as a set that is parsed."""
SOURCE = "DGII public e-CF: local use only, never redistributed"
"""The e-CF set's source, its licensing kept beside its digests."""
COVERAGE = (
    "vendor_name",
    "vendor_tax_id",
    "customer_tax_id",
    "document_id",
    "date_issue",
    "amount_total_net",
    "amount_total_tax",
    "amount_total_gross",
    "amount_tip",
    "amount_tax_selective",
    "amount_tax_other",
    "document_id_modified",
    "amount_retention_itbis",
    "amount_retention_isr",
)
"""Every fieldtype the parser reads, the paper set's coverage (#213)."""


class ECFError(Exception):
    """The content is not an e-CF of a type the parser reads."""


def read(content: bytes) -> Prediction:
    """The header of one e-CF, every covered fieldtype present or `None`.

    Raises `ECFError` naming what keeps the content from being read.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ECFError(f"not well-formed XML: {error}") from error
    if root.tag != "ECF":
        raise ECFError(f"the root element is {root.tag}, not ECF")
    kind = _text(root, "Encabezado/IdDoc/TipoeCF")
    if kind not in TYPES:
        raise ECFError(f"TipoeCF {kind} is not one of {', '.join(sorted(TYPES))}")
    issuer = (
        _text(root, "Encabezado/Emisor/RNCEmisor"),
        _text(root, "Encabezado/Emisor/RazonSocialEmisor"),
    )
    buyer = (
        _text(root, "Encabezado/Comprador/RNCComprador"),
        _text(root, "Encabezado/Comprador/RazonSocialComprador"),
    )
    vendor, customer = (buyer, issuer) if kind in BOUGHT else (issuer, buyer)
    totals = "Encabezado/Totales"
    taxes = root.findall(f"{totals}/ImpuestosAdicionales/ImpuestoAdicional")
    return Prediction(
        fields={
            "document_id": _text(root, "Encabezado/IdDoc/eNCF"),
            "date_issue": _date(_text(root, "Encabezado/Emisor/FechaEmision")),
            "vendor_name": vendor[1],
            "vendor_tax_id": vendor[0],
            "customer_tax_id": customer[0],
            "amount_total_net": _sum(
                _text(root, f"{totals}/MontoGravadoTotal"),
                _text(root, f"{totals}/MontoExento"),
            ),
            "amount_total_tax": _text(root, f"{totals}/TotalITBIS"),
            "amount_total_gross": _text(root, f"{totals}/MontoTotal"),
            "amount_tip": _sum(
                *(
                    _text(each, "OtrosImpuestosAdicionales")
                    for each in taxes
                    if _text(each, "TipoImpuesto") == TIP
                )
            ),
            "amount_tax_selective": _sum(
                *(
                    _text(each, tag)
                    for each in taxes
                    for tag in (
                        "MontoImpuestoSelectivoConsumoEspecifico",
                        "MontoImpuestoSelectivoConsumoAdvalorem",
                    )
                )
            ),
            "amount_tax_other": _sum(
                *(
                    _text(each, "OtrosImpuestosAdicionales")
                    for each in taxes
                    if _text(each, "TipoImpuesto") != TIP
                )
            ),
            "document_id_modified": _text(root, "InformacionReferencia/NCFModificado"),
            "amount_retention_itbis": _retained(
                root, f"{totals}/TotalITBISRetenido", "MontoITBISRetenido"
            ),
            "amount_retention_isr": _retained(
                root, f"{totals}/TotalISRRetencion", "MontoISRRetenido"
            ),
        }
    )


def _text(element: ET.Element, path: str) -> str | None:
    """The stripped text at `path`, or None when it is absent or empty."""
    found = element.find(path)
    if found is None or found.text is None or not found.text.strip():
        return None
    return found.text.strip()


def _date(text: str | None) -> str | None:
    if text is None:
        return None
    try:
        return datetime.strptime(text, "%d-%m-%Y").date().isoformat()
    except ValueError as error:
        raise ECFError(f"FechaEmision {text} is not dd-MM-yyyy") from error


def _sum(*texts: str | None) -> str | None:
    """The sum of the amounts present, written as `Decimal` writes it."""
    present = [each for each in texts if each is not None]
    if not present:
        return None
    return str(sum((_amount(each) for each in present), Decimal(0)))


def _amount(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ECFError(f"{text} is not an amount") from error


def _retained(root: ET.Element, header: str, line: str) -> str | None:
    printed = _text(root, header)
    if printed is not None:
        return printed
    return _sum(*_lines(root, f"DetallesItems/Item/Retencion/{line}"))


def _lines(root: ET.Element, path: str) -> Iterable[str | None]:
    return (
        each.text.strip() if each.text and each.text.strip() else None
        for each in root.findall(path)
    )


@dataclass(frozen=True)
class ECFExtractor:
    """The parser as a backend: one e-CF in, its header out, at $0."""

    model: str = MODEL

    def extract(self, document: Document) -> Extraction:
        started = time.perf_counter()
        try:
            content = document.path.read_bytes()
        except OSError as error:
            raise ExtractionError(
                f"cannot read {document.path}: {error}", retryable=False
            ) from error
        try:
            prediction = read(content)
        except ECFError as error:
            raise ExtractionError(
                f"{document.document_id} is not a readable e-CF: {error}",
                retryable=False,
            ) from error
        return Extraction(
            prediction=prediction,
            usage=NOTHING,
            cost=Decimal(0),
            latency=time.perf_counter() - started,
            served_model=None,
        )


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
