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
- **Computed**: 2 Tipo Id from the identifier's length (9 digits an RNC, 1;
  11 a cedula, 2), 8 and 9 the services and goods split of the net amount,
  10 their total, 15 ITBIS por adelantar (11 less 14).
- **Filing annotation** (`export.filing`): 3, 7, 13, 14, 17, 23 and whether
  the document is reported at all.

Columns 16 and 19, ITBIS and ISR percibido, are disabled until a norm sets a
perception regime (instructivo pp. 7 and 8) and are written 0.00.

Failures
--------

A document with no reading, or one lacking an NCF, an RNC or a date, fails
its month (#215 decision 6): that month gets no file and is listed with every
reason. So does an identifier of neither length, or an amount the file cannot
hold, rather than a guessed Tipo Id or a zero standing in for what was read.
A document with no date, or with one that does not read as a day, goes to a
"no month" row, which counts as one failed file. A document annotated not
reported is left out of both sides and listed apart (#213 decision 2).

A comprobante's date is read day first, `DD/MM/YYYY`, as Dominican
comprobantes print it, unless it starts with a four-digit year; the scorer's
month-first reading is DocILE's and would put 02/04/2026 in February.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from stdnum.do import ncf, rnc

from docmatch.export.filing import ALL, Filing, Share
from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import read_number

NO_MONTH = "no month"
"""The row of documents whose date is missing or unreadable."""

CENT = Decimal("0.01")
ZERO = Decimal("0")
LINE_BREAK = "\r\n"

TIPO_ID = {9: "1", 11: "2"}
"""Tipo Id by the identifier's length: 1 an RNC, 2 a cedula."""

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
    ncf_text = _value(values, "document_id")
    if ncf_text is None:
        reasons.append("no NCF")
    identifier = _value(values, "vendor_tax_id")
    tipo_id = None
    if identifier is None:
        reasons.append("no RNC")
    else:
        identifier = rnc.compact(identifier)
        tipo_id = TIPO_ID.get(len(identifier)) if identifier.isdigit() else None
        if tipo_id is None:
            reasons.append(f"RNC {identifier} is neither 9 nor 11 digits")
    date_text = _value(values, "date_issue")
    day = None if date_text is None else read_day(date_text)
    if date_text is None:
        reasons.append("no date")
    elif day is None:
        reasons.append(f"date {date_text} unreadable")
    amounts: dict[str, Decimal] = {}
    for fieldtype in AMOUNTS:
        text = _value(values, fieldtype)
        amount = ZERO if text is None else read_number(text)
        if amount is None:
            reasons.append(f"{fieldtype} {text} unreadable")
        else:
            amounts[fieldtype] = amount
    period = NO_MONTH if day is None else f"{day:%Y%m}"
    if reasons:
        return period, None, tuple(reasons)
    assert ncf_text is not None and identifier is not None and tipo_id is not None
    assert day is not None
    modified = _value(values, "document_id_modified")
    return (
        period,
        _fields(
            filing,
            identifier=identifier,
            tipo_id=tipo_id,
            ncf=ncf.compact(ncf_text),
            modified="" if modified is None else ncf.compact(modified),
            day=day,
            amounts=amounts,
        ),
        (),
    )


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


_DIGITS = re.compile(r"\d+")


def read_day(text: str) -> date | None:
    """A comprobante's date: `YYYY-MM-DD` when it leads with the year, else
    day first, a two-digit year in the 2000s; None for anything else."""
    numbers = _DIGITS.findall(text)
    if len(numbers) != 3:
        return None
    if len(numbers[0]) == 4:
        year, month, day = numbers
    else:
        day, month, year = numbers
    if len(year) == 2:
        year = f"20{year}"
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None
