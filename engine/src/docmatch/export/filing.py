"""A regional set's filing annotation: `filing.csv` beside its labels.

The filing annotation is what the taxpayer decides about a document when
reporting it, which the document does not print (#215, #213): whether it is
reported at all, what kind of purchase it is, how its net amount divides
between services and goods, when and how it was paid, how its ITBIS is
credited and which ISR retention applies. It is the same whichever reading a
606 is built from, the reading's or the labels control's, and it is never
scored, so the two files differ only in what was read.

One row per pinned document, keyed by filename, pinned by the manifest's
digest the way `labels.csv` is (`evals.regional.read_pinned_csv`). An e-CF
set whose manifest declares `filing_per: ecf_type` has one row per e-CF type
instead, in an `ecf_type` column (`E31`), applied to every document of that
type, the type being the `E` and two digits of the document's labeled e-NCF
(#213, #231). Every column below must be present and no other:

- `reported`: `yes` or `no`. A document not reported is left out of both
  606 files and needs no other choice; the owner's comprobantes without an
  NCF are annotated so (#213).
- `purchase_type`: the 606's column 3, 1 to 11.
- `split`: `services`, `goods`, or the services amount of a mixed document,
  the rest of the read net amount being goods.
- `payment_date`: empty, or the day paid as `YYYY-MM-DD`; written as the
  606's column 7. It picks the ISR retention rate; empty, the comprobante
  date does (#249).
- `payment_method`: the 606's column 23, 1 to 7.
- `itbis_proportional`, `itbis_to_cost`: empty for none, `all` for the whole
  ITBIS the document carries, or an amount; the 606's columns 13 and 14.
- `isr_retention_type`: empty, or the 606's column 17, 1 to 9.

The codes and their ranges are the instructivo's, "Llenado y Remisión del
Formato de Envío de Compras de Bienes y Servicios (606)", DGII, February 2026
edition (PDF last modified 2026-02-12, read 2026-10-23), pp. 5 to 9. An
amount is written plainly, digits with at most two decimals after a dot,
since it is typed by the owner and never read from a page.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from docmatch.evals.manifest import FILING_FILE, Manifest
from docmatch.evals.regional import (
    FILENAME,
    LABELS_FILE,
    RegionalError,
    read_labels,
    read_pinned_csv,
)

ECF_TYPE = "ecf_type"
"""The column keying a per-type annotation, in place of the filename."""

COLUMNS = (
    "reported",
    "purchase_type",
    "split",
    "payment_date",
    "payment_method",
    "itbis_proportional",
    "itbis_to_cost",
    "isr_retention_type",
)
"""Every column `filing.csv` holds beside the filename."""

PURCHASE_TYPES = range(1, 12)
PAYMENT_METHODS = range(1, 8)
ISR_RETENTION_TYPES = range(1, 10)

ALL: Literal["all"] = "all"
"""A share of the ITBIS that is the whole of what the document carries."""

Split = Literal["services", "goods"] | Decimal
"""Wholly services, wholly goods, or the services amount of a mixed document."""

Share = Literal["all"] | Decimal | None
"""None of the document's ITBIS, all of it, or an amount."""


@dataclass(frozen=True)
class Filing:
    """The taxpayer's choices about one document, for its row of a 606."""

    reported: bool
    purchase_type: int | None
    split: Split | None
    payment_date: date | None
    payment_method: int | None
    itbis_proportional: Share
    itbis_to_cost: Share
    isr_retention_type: int | None


def read_filing(manifest: Manifest, directory: Path) -> Mapping[str, Filing]:
    """Each pinned document's filing annotation, read from `directory`.

    Raises `RegionalError` naming the first thing that keeps the file from
    being the set's pinned annotation, or a choice the 606 does not allow.
    """
    path = directory / FILING_FILE
    if manifest.filing_per is None:
        _, rows = read_pinned_csv(manifest, directory, FILING_FILE, _check_columns)
        return {
            filename: _filing(row, f"{path}, {filename}")
            for filename, row in rows.items()
        }
    types = _ecf_types(manifest, directory)
    _, by_type = read_pinned_csv(
        manifest,
        directory,
        FILING_FILE,
        lambda header, where: _check_columns(header, where, ECF_TYPE),
        key=ECF_TYPE,
        keys=sorted(set(types.values())),
        unlisted="no document of the set has",
    )
    filings = {kind: _filing(row, f"{path}, {kind}") for kind, row in by_type.items()}
    return {filename: filings[kind] for filename, kind in types.items()}


_E_NCF = re.compile(r"E([0-9]{2})[0-9]{10}")


def _ecf_types(manifest: Manifest, directory: Path) -> dict[str, str]:
    """Each document's e-CF type, read from its labeled e-NCF."""
    labels = read_labels(manifest, directory)
    types: dict[str, str] = {}
    for filename in manifest.document_ids:
        numbers = labels.header(filename).get("document_id", ())
        match = _E_NCF.fullmatch(numbers[0]) if numbers else None
        if match is None:
            raise RegionalError(
                f"{directory / LABELS_FILE}: the label of {filename} names no e-CF "
                "type, an e-NCF being an E and twelve ASCII digits"
            )
        types[filename] = f"E{match.group(1)}"
    return types


def _check_columns(header: list[str], path: Path, key: str = FILENAME) -> None:
    unknown = [each for each in header if each not in (key, *COLUMNS)]
    if unknown:
        raise RegionalError(
            f"{path} has the column {', '.join(unknown)}, which a filing "
            "annotation does not hold"
        )
    missing = [each for each in COLUMNS if each not in header]
    if missing:
        raise RegionalError(f"{path} has no column {', '.join(missing)}")


def _filing(row: Mapping[str, str], where: str) -> Filing:
    cells = {column: row[column].strip() for column in COLUMNS}
    reported = cells["reported"].lower()
    if reported not in ("yes", "no"):
        raise RegionalError(
            f"{where}: reported is {cells['reported']!r}, not yes or no"
        )
    if reported == "no":
        return Filing(False, None, None, None, None, None, None, None)
    return Filing(
        reported=True,
        purchase_type=_code(cells, "purchase_type", PURCHASE_TYPES, where),
        split=_split(cells["split"], where),
        payment_date=_date(cells["payment_date"], where),
        payment_method=_code(cells, "payment_method", PAYMENT_METHODS, where),
        itbis_proportional=_share(cells, "itbis_proportional", where),
        itbis_to_cost=_share(cells, "itbis_to_cost", where),
        isr_retention_type=(
            _code(cells, "isr_retention_type", ISR_RETENTION_TYPES, where)
            if cells["isr_retention_type"]
            else None
        ),
    )


def _code(cells: Mapping[str, str], column: str, allowed: range, where: str) -> int:
    text = cells[column]
    if not text.isdigit() or int(text) not in allowed:
        raise RegionalError(
            f"{where}: {column} is {text!r}, not a code from {allowed.start} to "
            f"{allowed.stop - 1}"
        )
    return int(text)


_AMOUNT = re.compile(r"\A\d+(\.\d{1,2})?\Z")


def _amount(text: str, column: str, where: str) -> Decimal:
    if not _AMOUNT.match(text):
        raise RegionalError(
            f"{where}: {column} is {text!r}, not an amount written as digits with "
            "at most two decimals"
        )
    return Decimal(text)


def _split(text: str, where: str) -> Split:
    if text in ("services", "goods"):
        return "services" if text == "services" else "goods"
    if not text:
        raise RegionalError(
            f"{where}: split is empty, not services, goods or an amount"
        )
    return _amount(text, "split", where)


def _share(cells: Mapping[str, str], column: str, where: str) -> Share:
    text = cells[column]
    if not text:
        return None
    if text == ALL:
        return ALL
    return _amount(text, column, where)


def _date(text: str, where: str) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise RegionalError(
            f"{where}: payment_date is {text!r}, not a day written YYYY-MM-DD"
        ) from None
