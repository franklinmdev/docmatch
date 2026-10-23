"""The e-CF parser backend: an e-CF's XML read into a `Prediction`, at $0.

An e-CF is DGII's electronic comprobante, an XML document whose values are
already structured, so it is parsed and never sent to a model (#214, #222).
`--backend ecf` is this parser behind the `Extractor` seam: no vendor call,
no usage, no cost and no served model. Its F1 measures parsing, near 1.000
by construction, never extraction.

The mapping follows DGII's XSDs v1.0 of 16/10/2025 for the four types read
here, E31, E32, E41 and E43 (root `ECF`, read 2026-10-23), and
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
  format says the total is before ITBIS and additional taxes, each counted
  only when printed: an e-CF with no taxed amount, an E43's usual case, has
  its net from `MontoExento` alone, and one with neither has none;
  `amount_total_tax` is `TotalITBIS` and `amount_total_gross` `MontoTotal`.
- Additional taxes, by the format's Table I: an ISC amount (`Especifico` or
  `Advalorem`, codes 006 to 039) is `amount_tax_selective`; the
  `OtrosImpuestosAdicionales` of code 001, the legal tip, is `amount_tip`,
  and of any other code `amount_tax_other`. Each is summed over the header's
  `ImpuestosAdicionales`.
- `document_id_modified` is `InformacionReferencia/NCFModificado`, of which
  the XSDs allow one; several are refused.
- `amount_retention_itbis` and `amount_retention_isr` are the header's
  `TotalITBISRetenido` and `TotalISRRetencion` when printed, otherwise the
  sum of the lines' `Retencion` amounts, which the format says the header
  totals are.

The XSDs declare no `targetNamespace`, so an e-CF they validate has a root
`ECF` in no namespace, and a root in any namespace is refused as not an
e-CF rather than read by its local name. Every amount read must match the
XSDs' amount pattern. A sum is of the values present and absent when none
is. The header is all the parser reports: no line item is read, because
whether a line's `PrecioUnitarioItem` is net or gross of ITBIS is unverified
(#206), and the scorer covers no line-item fieldtype on a regional set. The
XMLDSig signature is not verified (#222).

The XML is parsed with the standard library's `xml.etree.ElementTree`
(Python 3.12 documentation, its "XML vulnerabilities" section read
2026-10-23): it expands no external entity and retrieves no DTD, and the
expat it binds, 2.8.5 here, is past 2.4.1, the version that stops the
billion laughs and quadratic blowup by capping internal entity expansion.
A supplier's e-CF is third-party input, so this matters even though every
file is pinned by digest before it is read.
"""

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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
            "amount_total_tax": _money(root, f"{totals}/TotalITBIS"),
            "amount_total_gross": _money(root, f"{totals}/MontoTotal"),
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
            "document_id_modified": _modified(root),
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


AMOUNT = re.compile(r"[0-9]{1,16}(\.[0-9]{1,2})?")
"""The XSDs' pattern for every amount read here
(`Decimal18D1or2ValidationTypeMayorIgualCero` and `...MayorCero`): digits
with up to two decimals, so no sign, exponent, comma, `NaN` or `Infinity`,
all of which `Decimal` would otherwise accept or misread."""


def _amount(text: str) -> Decimal:
    if not AMOUNT.fullmatch(text):
        raise ECFError(f"{text} is not an amount")
    return Decimal(text)


def _money(root: ET.Element, path: str) -> str | None:
    """The amount at `path` as printed, refused unless the XSDs allow it."""
    text = _text(root, path)
    if text is not None:
        _amount(text)
    return text


def _modified(root: ET.Element) -> str | None:
    """The one NCF this e-CF modifies. The XSDs allow one
    `InformacionReferencia` holding one `NCFModificado`, so several is not an
    e-CF they validate, and is refused rather than read as the first or as
    several values, either of which would score a document DGII rejects."""
    found = root.findall("InformacionReferencia/NCFModificado")
    if len(found) > 1:
        raise ECFError(f"{len(found)} NCFModificado where the XSDs allow one")
    return _text(root, "InformacionReferencia/NCFModificado")


def _retained(root: ET.Element, header: str, line: str) -> str | None:
    printed = _money(root, header)
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
