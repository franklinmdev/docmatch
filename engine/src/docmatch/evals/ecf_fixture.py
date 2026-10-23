"""The committed e-CF fixture: made-up e-CFs written to DGII's XSDs v1.0.

DGII publishes the XSD of every e-CF type and no sample XML (#206), and no
real e-CF may enter a commit (`CLAUDE.md` rule 6), so the e-CF set CI scores
is four e-CFs invented here, one of each type the parser reads: an E31
crédito fiscal carrying the legal tip, a selective and another additional tax
and a modified NCF; an E32 consumo to nobody, whose `Comprador` is empty and
which prints no exempt amount, the missing optional elements; an E41 compras
whose retentions are printed on its lines only; and an E43 gastos menores,
which names no buyer. Every name, number and amount is made up, and the
amounts are not computed at any tax rate the fixture would be stating: the
rates the XSD requires beside an additional tax are placeholders. The RNCs,
the cedula and the e-NCFs pass python-stdnum 2.2, as the paper fixture's do,
from bases picked by hand.

Each e-CF is written element by element in the order the XSDs of
16/10/2025 give (`e-CF 31 v.1.0.xsd` and its three siblings, read
2026-10-23), with the required elements of its type and nothing more but the
optional ones a fieldtype reads. The XSDs end every e-CF with one element of
any name after `FechaHoraFirma`, the XMLDSig signature's slot; here it is an
empty `Signature` in the XMLDSig namespace, which is not a signature, and the
parser never verifies one (#222).

Each e-CF's labels are written by hand beside it, not read back by the
parser, so the saved run scoring 1.000 against them says the parser reads
what was written. The saved run is the parser's own reading of the four
files, what `docmatch extract --backend ecf` saves, with a record whose
commit is left out so it does not change with every commit.

Regenerate with `uv run python -m docmatch.evals.ecf_fixture`.
"""

import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from stdnum import luhn
from stdnum.do import rnc

from docmatch.evals.ecf import COVERAGE, MODEL, REGION, read, write_set
from docmatch.evals.manifest import Manifest, write
from docmatch.evals.run import MANIFEST_FILE, PREDICTIONS_FILE, RECORD_FILE

FIXTURE = Path(__file__).parents[3] / "tests" / "evals" / "regional"
SET = "ecf"
"""The e-CF set's directory beside the paper set's."""
RUN = "ecf-run"
SOURCE = "synthetic"
XSD_DIGESTS = {
    31: "6f2909a93d84919518d2ae3c77fead4b35c3e8c95996b8af67b0040c2e2be298",
    32: "151676df03c61335b4ac1eca97f99395488d57ea92793b49353602871e651f3c",
    41: "eae1993c637375bc1cbe80932411d87f5680cd54e77e4d1b2752d72a6c8b2ab3",
    43: "776f030980c2c50cf0221e9263c55f367c8631727adff00ab45e2c7c1abafc52",
}
"""The sha256 of each XSD v1.0 of 16/10/2025 as DGII served it on 2026-10-23,
saved as `e<type>.xsd`. They are not committed, since DGII states no terms for
redistributing them; CI downloads them and the validation test checks these
first, so a schema DGII changes is noticed rather than validated against."""
XSD_TYPOS = {31: ("unknown type 'IndicadorServicioTodoIncluidoType'",)}
"""What each XSD gets wrong as DGII serves it, which `xmlschema` reports
while building it in lax mode. E31 names the simple type of
`IndicadorServicioTodoIncluido` with a leading space, so the element it types
is unknown; E32 spells it right. No fixture e-CF carries that element."""
XMLDSIG = "http://www.w3.org/2000/09/xmldsig#"


def _rnc(base: str) -> str:
    return base + rnc.calc_check_digit(base)


def _cedula(base: str) -> str:
    return base + luhn.calc_check_digit(base)


def labeled(**values: str) -> dict[str, str]:
    """Every covered fieldtype to its value, empty where the e-CF has none."""
    return {fieldtype: values.get(fieldtype, "") for fieldtype in COVERAGE}


@dataclass(frozen=True)
class Line:
    """One `Item`: what the XSDs require of it, and its printed retentions."""

    name: str
    quantity: str
    price: str
    amount: str
    billing: str = "1"
    """`IndicadorFacturacion`: 1 to 3 the ITBIS rate it is taxed at, 4 exempt."""
    service: bool = False
    itbis_retained: str | None = None
    isr_retained: str | None = None


@dataclass(frozen=True)
class Additional:
    """One `ImpuestoAdicional` of the header's `ImpuestosAdicionales`."""

    code: str
    """`TipoImpuesto`, a code of the format's Table I."""
    rate: str
    specific: str | None = None
    advalorem: str | None = None
    other: str | None = None


@dataclass(frozen=True)
class ECF:
    """One made-up e-CF: its file, its values, and its labels written by hand."""

    filename: str
    kind: int
    """`TipoeCF`: 31, 32, 41 or 43."""
    encf: str
    issuer_rnc: str
    issuer_name: str
    issued: str
    """`FechaEmision`, `dd-MM-yyyy` as the XSDs write a date."""
    lines: tuple[Line, ...]
    total: str
    labels: dict[str, str]
    buyer_rnc: str | None = None
    buyer_name: str | None = None
    taxed: str | None = None
    exempt: str | None = None
    itbis: str | None = None
    additional: tuple[Additional, ...] = ()
    additional_total: str | None = None
    retained_itbis: str | None = None
    retained_isr: str | None = None
    modified: str | None = None
    modified_on: str | None = None
    modification: str | None = None
    signed: str = "10-06-2026 10:00:00"


ECFS = (
    ECF(
        filename="ecf0001.xml",
        kind=31,
        encf="E310000000001",
        issuer_rnc=_rnc("13099005"),
        issuer_name="Distribuidora Ficticia del Cibao SRL",
        issued="10-06-2026",
        buyer_rnc=_rnc("13099006"),
        buyer_name="Hotel Imaginario Playa SRL",
        taxed="10000.00",
        exempt="500.00",
        itbis="1800.00",
        additional=(
            Additional(code="001", rate="10", other="1000.00"),
            Additional(code="002", rate="2", other="200.00"),
            Additional(code="023", rate="10", advalorem="150.00"),
        ),
        additional_total="1350.00",
        total="13650.00",
        modified="B0100000005",
        modified_on="09-06-2026",
        modification="4",
        lines=(
            Line(
                name="Servicio de catering",
                quantity="1",
                price="10000.00",
                amount="10000.00",
                service=True,
            ),
            Line(
                name="Agua embotellada",
                quantity="50",
                price="10.00",
                amount="500.00",
                billing="4",
            ),
        ),
        labels=labeled(
            document_id="E310000000001",
            date_issue="2026-06-10",
            vendor_name="Distribuidora Ficticia del Cibao SRL",
            vendor_tax_id="130990052",
            customer_tax_id="130990069",
            amount_total_net="10500.00",
            amount_total_tax="1800.00",
            amount_total_gross="13650.00",
            amount_tip="1000.00",
            amount_tax_selective="150.00",
            amount_tax_other="200.00",
            document_id_modified="B0100000005",
        ),
    ),
    ECF(
        filename="ecf0002.xml",
        kind=32,
        encf="E320000000001",
        issuer_rnc=_rnc("13099007"),
        issuer_name="Supermercado Inventado SRL",
        issued="11-06-2026",
        taxed="1000.00",
        itbis="180.00",
        total="1180.00",
        lines=(
            Line(name="Arroz selecto", quantity="10", price="100.00", amount="1000.00"),
        ),
        labels=labeled(
            document_id="E320000000001",
            date_issue="2026-06-11",
            vendor_name="Supermercado Inventado SRL",
            vendor_tax_id="130990077",
            amount_total_net="1000.00",
            amount_total_tax="180.00",
            amount_total_gross="1180.00",
        ),
    ),
    ECF(
        filename="ecf0003.xml",
        kind=41,
        encf="E410000000001",
        issuer_rnc=_rnc("13099008"),
        issuer_name="Constructora Ficticia del Sur SRL",
        issued="12-06-2026",
        buyer_rnc=_cedula("0009900002"),
        buyer_name="Juan Ejemplo",
        taxed="5000.00",
        itbis="900.00",
        total="5900.00",
        lines=(
            Line(
                name="Pintura de fachada",
                quantity="1",
                price="2500.00",
                amount="2500.00",
                service=True,
                itbis_retained="450.00",
                isr_retained="250.00",
            ),
            Line(
                name="Reparacion de techo",
                quantity="1",
                price="2500.00",
                amount="2500.00",
                service=True,
                itbis_retained="450.00",
                isr_retained="250.00",
            ),
        ),
        labels=labeled(
            document_id="E410000000001",
            date_issue="2026-06-12",
            vendor_name="Juan Ejemplo",
            vendor_tax_id="00099000028",
            customer_tax_id="130990085",
            amount_total_net="5000.00",
            amount_total_tax="900.00",
            amount_total_gross="5900.00",
            amount_retention_itbis="900.00",
            amount_retention_isr="500.00",
        ),
    ),
    ECF(
        filename="ecf0004.xml",
        kind=43,
        encf="E430000000001",
        issuer_rnc=_rnc("13099008"),
        issuer_name="Constructora Ficticia del Sur SRL",
        issued="13-06-2026",
        exempt="350.00",
        total="350.00",
        lines=(
            Line(
                name="Peaje",
                quantity="1",
                price="350.00",
                amount="350.00",
                billing="4",
                service=True,
            ),
        ),
        labels=labeled(
            document_id="E430000000001",
            date_issue="2026-06-13",
            customer_tax_id="130990085",
            amount_total_net="350.00",
            amount_total_gross="350.00",
        ),
    ),
)


def _add(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    child = ET.SubElement(parent, tag)
    if text is not None:
        child.text = text
    return child


def _some(parent: ET.Element, *pairs: tuple[str, str | None]) -> None:
    """Each element whose value is given, in the order given."""
    for tag, text in pairs:
        if text is not None:
            _add(parent, tag, text)


def to_xml(ecf: ECF) -> bytes:
    """The e-CF as its XSD orders it, UTF-8 with a declaration."""
    root = ET.Element("ECF")
    header = _add(root, "Encabezado")
    _add(header, "Version", "1.0")
    _id_doc(_add(header, "IdDoc"), ecf)
    issuer = _add(header, "Emisor")
    _add(issuer, "RNCEmisor", ecf.issuer_rnc)
    _add(issuer, "RazonSocialEmisor", ecf.issuer_name)
    _add(issuer, "DireccionEmisor", "Calle Inventada 1")
    _add(issuer, "FechaEmision", ecf.issued)
    if ecf.kind != 43:
        buyer = _add(header, "Comprador")
        _some(
            buyer,
            ("RNCComprador", ecf.buyer_rnc),
            ("RazonSocialComprador", ecf.buyer_name),
        )
    _totals(_add(header, "Totales"), ecf)
    items = _add(root, "DetallesItems")
    for number, line in enumerate(ecf.lines, start=1):
        _item(_add(items, "Item"), number, line)
    if ecf.modified is not None:
        reference = _add(root, "InformacionReferencia")
        _some(
            reference,
            ("NCFModificado", ecf.modified),
            ("FechaNCFModificado", ecf.modified_on),
            ("CodigoModificacion", ecf.modification),
        )
    _add(root, "FechaHoraFirma", ecf.signed)
    ET.SubElement(root, "Signature", xmlns=XMLDSIG)
    ET.indent(root)
    written: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return written + b"\n"


def _id_doc(id_doc: ET.Element, ecf: ECF) -> None:
    _add(id_doc, "TipoeCF", str(ecf.kind))
    _add(id_doc, "eNCF", ecf.encf)
    if ecf.kind != 32:
        _add(id_doc, "FechaVencimientoSecuencia", "31-12-2027")
    if ecf.kind in (31, 32):
        _add(id_doc, "IndicadorMontoGravado", "0")
        _add(id_doc, "TipoIngresos", "01")
    _add(id_doc, "TipoPago", "1")


def _totals(totals: ET.Element, ecf: ECF) -> None:
    rated = ecf.taxed is not None
    _some(
        totals,
        ("MontoGravadoTotal", ecf.taxed),
        ("MontoGravadoI1", ecf.taxed),
        ("MontoExento", ecf.exempt),
        ("ITBIS1", "18" if rated else None),
        ("TotalITBIS", ecf.itbis),
        ("TotalITBIS1", ecf.itbis),
        ("MontoImpuestoAdicional", ecf.additional_total),
    )
    if ecf.additional:
        taxes = _add(totals, "ImpuestosAdicionales")
        for each in ecf.additional:
            _some(
                _add(taxes, "ImpuestoAdicional"),
                ("TipoImpuesto", each.code),
                ("TasaImpuestoAdicional", each.rate),
                ("MontoImpuestoSelectivoConsumoEspecifico", each.specific),
                ("MontoImpuestoSelectivoConsumoAdvalorem", each.advalorem),
                ("OtrosImpuestosAdicionales", each.other),
            )
    _some(
        totals,
        ("MontoTotal", ecf.total),
        ("TotalITBISRetenido", ecf.retained_itbis),
        ("TotalISRRetencion", ecf.retained_isr),
    )


def _item(item: ET.Element, number: int, line: Line) -> None:
    _add(item, "NumeroLinea", str(number))
    _add(item, "IndicadorFacturacion", line.billing)
    if line.itbis_retained is not None or line.isr_retained is not None:
        _some(
            _add(item, "Retencion"),
            ("IndicadorAgenteRetencionoPercepcion", "1"),
            ("MontoITBISRetenido", line.itbis_retained),
            ("MontoISRRetenido", line.isr_retained),
        )
    _add(item, "NombreItem", line.name)
    _add(item, "IndicadorBienoServicio", "2" if line.service else "1")
    _add(item, "CantidadItem", line.quantity)
    _add(item, "PrecioUnitarioItem", line.price)
    _add(item, "MontoItem", line.amount)


def write_fixture_set(directory: Path) -> Manifest:
    """Write every e-CF into `directory`, with its hand-written labels and the
    set's manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    contents = {each.filename: to_xml(each) for each in ECFS}
    for filename, content in contents.items():
        (directory / filename).write_bytes(content)
    return write_set(
        directory,
        contents,
        {each.filename: each.labels for each in ECFS},
        source=SOURCE,
    )


def write_run(directory: Path, manifest: Manifest) -> None:
    """Save the parser's reading of every e-CF as a run, the way `extract`
    writes its predictions, with a record of the backend, model and region."""
    directory.mkdir(parents=True, exist_ok=True)
    readings = {
        each.filename: read(to_xml(each)).model_dump(exclude_defaults=True)
        for each in ECFS
    }
    (directory / PREDICTIONS_FILE).write_text(
        json.dumps(readings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    record = {"backend": "ecf", "requested_model": MODEL, "region": REGION}
    (directory / RECORD_FILE).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    write(manifest, directory / MANIFEST_FILE)


def main(directory: Path = FIXTURE) -> None:
    manifest = write_fixture_set(directory / SET)
    write_run(directory / RUN, manifest)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE)
