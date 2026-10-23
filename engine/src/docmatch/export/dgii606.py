"""The 606, DGII's monthly report of purchases, built from a saved run (#215).

`export_606` is pure: from each document's reading, its labels and its filing
annotation it builds two sides, the reading's and the labels control's, each
one 606 per `AAAAMM` of its own comprobante dates, so a misread date moves a
row between months and shows as a difference. The control differs from the
reading only in what was read, since the annotation is the same for both.

Format
------

The instructivo, "Llenado y Remisión del Formato de Envío de Compras de
Bienes y Servicios (606)", DGII, February 2026 edition (PDF last modified
2026-02-12, read 2026-10-23), pp. 5 to 9, names the 23 columns of a line and
the file DGII's generator writes, `DGII_F_606_<RNC>_<AAAAMM>.TXT` (p. 10).
The pre-validator run on #217 (MSI 2.6) fixed the rest: a header
`606|<RNC>|<AAAAMM>|<lines>`, then one pipe-delimited line of exactly 23
fields per comprobante, amounts with two decimals; CRLF or LF, with or
without a newline after the last line, all pass. This writes CRLF between
lines and none after the last, the shape #217's `good-v1.txt` passed in.

Columns, by source (#215 decision 3):

- **Read**, from the reading or the labels: 1 RNC or cedula (`vendor_tax_id`),
  4 NCF (`document_id`), 5 modified NCF, 6 date, the net amount the split
  divides, 11 ITBIS (`amount_total_tax`), 12 and 18 the ITBIS and ISR
  retentions where the comprobante prints them, 20 selective tax, 21 other
  taxes, 22 legal tip. A retention not printed is 0 here; computing it from
  the annotation at the rate in force is #230's.
- **Computed**: 2 Tipo Id, 1 for a number python-stdnum 2.2 validates as an
  RNC and 2 for one it validates as a cedula, 8 and 9 the services and goods
  split of the net amount, 10 their total, 15 ITBIS por adelantar (11 less
  14, the instructivo's p. 7).
- **Filing annotation** (`export.filing`): 3, 7, 13, 14, 17, 23 and whether
  the document is reported at all.

Columns 16 and 19, ITBIS and ISR percibido, are disabled until a norm sets a
perception regime (instructivo pp. 7 and 8) and are written 0.00. Amounts
are written with a dot and two decimals, as the instructivo asks (p. 9,
"RD$10.18"); it names no rounding, so a third decimal rounds half up.

Failures
--------

A document lacking an NCF, an RNC or a date fails its month (#215 decision
6): that month gets no file and is listed with every reason, and its other
rows are withheld with it. A document with no date, or with one that does
not read as a day, goes to a "no month" row, which counts as one failed
file; so does a document with no reading, which has no date to place it by,
so on the reading side it fails "no month" while the control files it in its
month. A document annotated not reported is left out of both sides and
listed apart (#213 decision 2).

A month also fails rather than hold a value the file should not: an RNC,
cedula, NCF or modified NCF python-stdnum 2.2 does not validate (which also
keeps a pipe, a character outside ASCII and a wrong length out of the file),
an amount that does not read, any negative amount, a services split above
the net amount, and an ITBIS retention, proportional share or share to cost
above the ITBIS, the last also what would make column 15 negative. The
instructivo names no sign convention, for a nota de credito or anything else,
so nothing is ever written negative. No reason quotes a value.

A comprobante's date is read day first, `DD/MM/YYYY`, as Dominican
comprobantes print it, unless it starts with a four-digit year; the scorer's
month-first reading is DocILE's and would put 02/04/2026 in February.
"""

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from stdnum.do import cedula, ncf, rnc

from docmatch.export.filing import ALL, Filing, Share
from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import read_date, read_number, reads_day_first

REGION = "do"
"""The region whose sets a 606 is filed for."""

NOT_REPORTED = "annotated not reported"
"""Why a document is in neither side's files."""

NO_MONTH = "no month"
"""The row of documents whose date is missing or unreadable."""

CENT = Decimal("0.01")
ZERO = Decimal("0")
LINE_BREAK = "\r\n"

AMOUNTS = (
    "amount_total_net",
    "amount_total_tax",
    "amount_retention_itbis",
    "amount_retention_isr",
    "amount_tax_selective",
    "amount_tax_other",
    "amount_tip",
)
"""The amounts a line reads, each 0 when the comprobante carries none."""


@dataclass(frozen=True)
class Failure:
    """A document that keeps its month from being filed, and why."""

    filename: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Month:
    """One period of one side: its lines, or the documents that failed it."""

    period: str
    """`AAAAMM`, or `NO_MONTH`."""
    lines: tuple[str, ...]
    failures: tuple[Failure, ...]

    @property
    def is_failed(self) -> bool:
        return bool(self.failures) or self.period == NO_MONTH


@dataclass(frozen=True)
class Side:
    """The 606 files of one side, the reading's or the labels control's."""

    reporter: str
    months: tuple[Month, ...]
    """In order of period, the no month row last."""

    @property
    def failed(self) -> tuple[Month, ...]:
        return tuple(month for month in self.months if month.is_failed)

    def files(self) -> dict[str, str]:
        """Each month that did not fail, as its filename and its content."""
        return {
            filename(self.reporter, month.period): LINE_BREAK.join(
                (f"606|{self.reporter}|{month.period}|{len(month.lines)}", *month.lines)
            )
            for month in self.months
            if not month.is_failed
        }


@dataclass(frozen=True)
class Export:
    """Both sides of a run's 606, and the documents neither reports."""

    reading: Side
    control: Side
    not_reported: tuple[str, ...]

    def sides(self) -> tuple[tuple[str, str, Side], ...]:
        """Each side's directory under the export, its heading in the report,
        and the side."""
        return (
            ("reading", "Reading", self.reading),
            ("control", "Labels control", self.control),
        )


def filename(reporter: str, period: str) -> str:
    """The name DGII's own generator gives a 606 file."""
    return f"DGII_F_606_{reporter}_{period}.TXT"


def export_606(
    reporter: str,
    readings: Mapping[str, FieldValues],
    labels: Mapping[str, FieldValues],
    filing: Mapping[str, Filing],
) -> Export:
    """The 606 of every document `filing` annotates, from what was read and
    from the labels; a document missing from `readings` was not read.

    Documents keep the order `filing` gives them, within each file.
    """
    reported = [name for name, each in filing.items() if each.reported]
    return Export(
        reading=_side(reporter, reported, readings, filing),
        control=_side(reporter, reported, labels, filing),
        not_reported=tuple(name for name, each in filing.items() if not each.reported),
    )


def _side(
    reporter: str,
    documents: Sequence[str],
    values: Mapping[str, FieldValues],
    filing: Mapping[str, Filing],
) -> Side:
    lines: dict[str, list[str]] = {}
    failures: dict[str, list[Failure]] = {}
    for name in documents:
        period, line, reasons = _line(values.get(name), filing[name])
        lines.setdefault(period, [])
        failures.setdefault(period, [])
        if reasons:
            failures[period].append(Failure(name, reasons))
        else:
            assert line is not None
            lines[period].append(line)
    periods = sorted(lines, key=lambda period: (period == NO_MONTH, period))
    return Side(
        reporter=reporter,
        months=tuple(
            Month(period, tuple(lines[period]), tuple(failures[period]))
            for period in periods
        ),
    )


def _line(
    values: FieldValues | None, filing: Filing
) -> tuple[str, str | None, tuple[str, ...]]:
    """A document's period, and its line or the reasons it has none."""
    if values is None:
        return NO_MONTH, None, ("no reading",)
    reasons: list[str] = []
    number = _ncf(values, "document_id", "NCF", reasons)
    identifier = _value(values, "vendor_tax_id")
    tipo_id = None
    if identifier is None:
        reasons.append("no RNC")
    else:
        identifier = rnc.compact(identifier)
        tipo_id = (
            "1"
            if rnc.is_valid(identifier)
            else "2"
            if cedula.is_valid(identifier)
            else None
        )
        if tipo_id is None:
            reasons.append("RNC or cedula invalid")
    modified = _ncf(values, "document_id_modified", "modified NCF", reasons)
    date_text = _value(values, "date_issue")
    day = (
        None
        if date_text is None
        else read_date(date_text, day_first=reads_day_first(REGION))
    )
    if date_text is None:
        reasons.append("no date")
    elif day is None:
        reasons.append("date unreadable")
    amounts: dict[str, Decimal] = {}
    for fieldtype in AMOUNTS:
        text = _value(values, fieldtype)
        amount = ZERO if text is None else read_number(text)
        if amount is None:
            reasons.append(f"{fieldtype} unreadable")
        elif amount < 0:
            reasons.append(f"{fieldtype} negative")
        else:
            amounts[fieldtype] = amount
    reasons.extend(_beyond_bounds(amounts, filing))
    period = NO_MONTH if day is None else f"{day:%Y%m}"
    if reasons:
        return period, None, tuple(reasons)
    assert number is not None and identifier is not None and tipo_id is not None
    assert day is not None
    return (
        period,
        _fields(
            filing,
            identifier=identifier,
            tipo_id=tipo_id,
            ncf=number,
            modified="" if modified is None else modified,
            day=day,
            amounts=amounts,
        ),
        (),
    )


def _ncf(
    values: FieldValues, fieldtype: str, name: str, reasons: list[str]
) -> str | None:
    """The fieldtype's NCF or e-NCF compacted, or None with the reason why
    not; a modified NCF is optional, so only its absence is not a reason."""
    text = _value(values, fieldtype)
    if text is None:
        if fieldtype == "document_id":
            reasons.append(f"no {name}")
        return None
    number = ncf.compact(text)
    if not ncf.is_valid(number):
        reasons.append(f"{name} invalid")
        return None
    return number


def _beyond_bounds(amounts: Mapping[str, Decimal], filing: Filing) -> list[str]:
    """Why a line built from these amounts would hold a column out of bounds:
    a services split above the net, or an ITBIS retention or share above the
    ITBIS, the last also what would turn column 15 negative."""
    net = amounts.get("amount_total_net")
    itbis = amounts.get("amount_total_tax")
    reasons = []
    if isinstance(filing.split, Decimal) and net is not None and filing.split > net:
        reasons.append("services split above the net amount")
    if itbis is None:
        return reasons
    retention = amounts.get("amount_retention_itbis")
    if retention is not None and retention > itbis:
        reasons.append("ITBIS retention above the ITBIS")
    for share, name in (
        (filing.itbis_proportional, "ITBIS to proportionality"),
        (filing.itbis_to_cost, "ITBIS to cost"),
    ):
        if share is not None and _share(share, itbis) > itbis:
            reasons.append(f"{name} above the ITBIS")
    return reasons


def _fields(
    filing: Filing,
    *,
    identifier: str,
    tipo_id: str,
    ncf: str,
    modified: str,
    day: date,
    amounts: Mapping[str, Decimal],
) -> str:
    assert filing.purchase_type is not None and filing.payment_method is not None
    net = amounts["amount_total_net"]
    itbis = amounts["amount_total_tax"]
    services = (
        net
        if filing.split == "services"
        else ZERO
        if filing.split == "goods"
        else filing.split
    )
    assert services is not None
    goods = net - services
    to_cost = _share(filing.itbis_to_cost, itbis)
    fields = (
        identifier,
        tipo_id,
        f"{filing.purchase_type:02d}",
        ncf,
        modified,
        f"{day:%Y%m%d}",
        "" if filing.payment_date is None else f"{filing.payment_date:%Y%m%d}",
        _money(services),
        _money(goods),
        _money(services + goods),
        _money(itbis),
        _money(amounts["amount_retention_itbis"]),
        _money(_share(filing.itbis_proportional, itbis)),
        _money(to_cost),
        _money(itbis - to_cost),
        _money(ZERO),
        "" if filing.isr_retention_type is None else f"{filing.isr_retention_type:02d}",
        _money(amounts["amount_retention_isr"]),
        _money(ZERO),
        _money(amounts["amount_tax_selective"]),
        _money(amounts["amount_tax_other"]),
        _money(amounts["amount_tip"]),
        str(filing.payment_method),
    )
    return "|".join(fields)


def _share(share: Share, itbis: Decimal) -> Decimal:
    if share is None:
        return ZERO
    return itbis if share == ALL else share


def _money(amount: Decimal) -> str:
    return f"{amount.quantize(CENT, rounding=ROUND_HALF_UP):f}"


def _value(values: FieldValues, fieldtype: str) -> str | None:
    """The first value read for a fieldtype, or None when there is none."""
    texts = [text.strip() for text in values.get(fieldtype, ()) if text.strip()]
    return texts[0] if texts else None


EXPORT_FILE = "export.json"
"""What `export_606` wrote, month by month, for `prevalidate 606` (#229)."""


class ExportError(Exception):
    """An export that cannot be written where it was asked to go."""


def write_export(exported: Export, out: Path, manifest: Path, run: Path) -> None:
    """Write both sides' files under `out` and list every month in
    `export.json`; `out` must be new or empty, so no file of an earlier
    export is ever read as this one's."""
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ExportError(f"{out} is not empty; give a new directory to export into")
    listed: dict[str, object] = {
        "manifest": str(manifest),
        "run": str(run),
        "not_reported": [
            {"filename": name, "reason": NOT_REPORTED} for name in exported.not_reported
        ],
    }
    for directory, _, side in exported.sides():
        (out / directory).mkdir(parents=True)
        for name, content in side.files().items():
            (out / directory / name).write_bytes(content.encode("ascii"))
        listed[directory] = [
            {
                "period": month.period,
                "file": None
                if month.is_failed
                else f"{directory}/{filename(side.reporter, month.period)}",
                "rows": len(month.lines),
                "failures": [
                    {"filename": each.filename, "reasons": list(each.reasons)}
                    for each in month.failures
                ],
            }
            for month in side.months
        ]
    (out / EXPORT_FILE).write_text(json.dumps(listed, indent=2) + "\n")


def render_export(exported: Export, out: Path, manifest: Path, run: Path) -> str:
    """The export as counts, periods and reasons: never a number, a name or a
    filename from a comprobante, so the owner's report can be shown."""
    documents = sum(
        len(month.lines) + len(month.failures) for month in exported.reading.months
    )
    lines = [
        "606 export",
        f"  manifest      {manifest}",
        f"  run           {run}",
        f"  out           {out}",
        f"  documents     {documents + len(exported.not_reported)}",
        f"  not reported  {len(exported.not_reported)}",
        "",
    ]
    for _, heading, side in exported.sides():
        written = len(side.months) - len(side.failed)
        lines.append(
            f"{heading}, {written} of {len(side.months)} monthly files written"
        )
        width = max(len(month.period) for month in side.months) if side.months else 0
        for month in side.months:
            lines.append(f"  {month.period:<{width}}  {_month(month)}")
        lines.append("")
    return "\n".join(lines)


def _month(month: Month) -> str:
    if not month.is_failed:
        return f"{len(month.lines)} {'row' if len(month.lines) == 1 else 'rows'}"
    reasons = Counter(reason for each in month.failures for reason in each.reasons)
    tally = ", ".join(
        reason if count == 1 else f"{reason} {count}"
        for reason, count in reasons.items()
    )
    total = len(month.lines) + len(month.failures)
    failed = len(month.failures)
    documents = "document" if total == 1 else "documents"
    share = (
        f"{failed} {documents}"
        if failed == total
        else f"{failed} of {total} {documents}"
    )
    return f"failed, {share}: {tally}"
