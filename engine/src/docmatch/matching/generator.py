"""Building cases from seeds: purchase orders and receiving records derived
from labeled invoices, with discrepancies injected and a known truth.

DocILE labels invoices only, so every purchase order and receiving record is
derived from a seed, a labeled invoice with lines, and every discrepancy is
injected into those two records with the truth written down beside the case.
The invoice is never altered: it stays the seed's labels, or a backend's
reading of the same document on the end-to-end row (#67). A document with no
labeled lines seeds nothing.

Seeds
-----

A seed's purchase order copies each line as labeled, and nothing is filled
in: a line labeled with only a description and an amount is a purchase-order
line with only those, and a discrepancy type is injected only on lines that
carry the cells it touches (#62). Lines whose own arithmetic fails stay as
seeds, since matching compares field to field and an invoice's arithmetic is
the gate's business. The receiving record copies quantities, units, codes
and descriptions, never prices, and each of its lines names the
purchase-order line it was received against, as an ERP's does (#63).

Injection
---------

Price variance lowers the purchase order's unit price where the seed line
carries one, else its amount, so the invoice ends up billed above the
purchase order (#65). Each injection draws near the edge or far past it,
half and half, in the bands `tolerances` defines, so that borderline misses
show in recall (#66, #70). The lowered value is written with the seed value's
own decimal places, and redrawn until it lands in its band after rounding;
a line where no value in the band exists, which is a small value where the
cent floor swallows the band, is not eligible for that band.

Sizes and the pinned seed
-------------------------

At least `FINDINGS_PER_TYPE` injected findings per type and `CLEAN_CASES`
clean cases, from one random seed pinned in code, so that a rate near 0.95
reads to about plus or minus 2 points (#66). Seeds are reused with fresh
draws where a pool is smaller than the count. Cases are rebuilt from the
labels on every run and never saved, so rerunning the command at the linked
commit reproduces the number and no document content is written anywhere.

The draw is `random.Random` on Python 3.12, whose Mersenne Twister stream
CPython promises; `uv.lock` pins the interpreter the numbers were produced
with.
"""

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from docmatch.matching.matcher import DiscrepancyType, Place
from docmatch.matching.records import (
    CELL_FIELDTYPES,
    Cell,
    ReceiptLine,
    ReceivingRecord,
    Record,
    cell_values,
)
from docmatch.matching.tolerances import (
    FAR_PERCENT,
    NEAR_PERCENT,
    PRICE,
    Band,
    Tolerance,
    band,
)
from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import read_number

SEED = 20260919
"""The random seed every case is rebuilt from. Changing it draws new cases."""

CLEAN_CASES = 1000
FINDINGS_PER_TYPE = 500

INJECTED_TYPES: tuple[DiscrepancyType, ...] = ("price variance",)
"""The types the generator injects, in the order their cases are built."""

RECEIPT_CELLS: tuple[Cell, ...] = ("quantity", "unit", "code", "description")
"""What a receiving record copies from a purchase-order line: never prices."""


class GeneratorError(Exception):
    """The seed pool cannot yield the cases asked for."""


@dataclass(frozen=True)
class Seed:
    """A labeled invoice a purchase order and receiving record are derived from."""

    document_id: str
    invoice: Record


@dataclass(frozen=True)
class Injected:
    """One discrepancy the generator put into a case: what the scorer holds as truth."""

    type: DiscrepancyType
    place: Place
    band: Band


@dataclass(frozen=True)
class Case:
    """One seed with its derived records, either clean or carrying injections."""

    document_id: str
    invoice: Record
    purchase_order: Record
    receipt: ReceivingRecord
    truth: tuple[Injected, ...]

    @property
    def is_clean(self) -> bool:
        return not self.truth


def generate(
    seeds: Sequence[Seed],
    *,
    seed: int = SEED,
    clean: int = CLEAN_CASES,
    per_type: int = FINDINGS_PER_TYPE,
) -> tuple[Case, ...]:
    """`clean` clean cases, then cases carrying `per_type` findings of each
    injected type, all from one draw seeded with `seed`."""
    rng = random.Random(seed)
    pool = [each for each in seeds if each.invoice.lines]
    cases = [_clean(each) for each in _cycle(pool, rng, clean)]
    for type_ in INJECTED_TYPES:
        cases += _injected(pool, type_, rng, per_type)
    return tuple(cases)


def eligible(seed: Seed, type_: DiscrepancyType) -> tuple[int, ...]:
    """The positions of the seed's lines a type can be injected on."""
    return tuple(
        position
        for position, line in enumerate(seed.invoice.lines)
        if _priced(line) is not None
    )


def documents_carrying(seeds: Iterable[Seed], type_: DiscrepancyType) -> int:
    """How many seeds a type can be injected on at all."""
    return sum(1 for each in seeds if eligible(each, type_))


def _cycle(pool: Sequence[Seed], rng: random.Random, count: int) -> list[Seed]:
    """`count` seeds in a shuffled order, going round again when the pool is
    smaller than the count."""
    drawn: list[Seed] = []
    if not pool:
        return drawn
    while len(drawn) < count:
        order = list(pool)
        rng.shuffle(order)
        drawn += order[: count - len(drawn)]
    return drawn


def _clean(seed: Seed) -> Case:
    return _case(seed, seed.invoice.lines, ())


def _case(
    seed: Seed, po_lines: Sequence[FieldValues], truth: tuple[Injected, ...]
) -> Case:
    purchase_order = Record(header=seed.invoice.header, lines=tuple(po_lines))
    receipt = ReceivingRecord(
        tuple(
            ReceiptLine(cells=_received(line), po_line=position)
            for position, line in enumerate(po_lines)
        )
    )
    return Case(seed.document_id, seed.invoice, purchase_order, receipt, truth)


def _received(po_line: FieldValues) -> FieldValues:
    return {
        fieldtype: tuple(po_line[fieldtype])
        for cell in RECEIPT_CELLS
        for fieldtype in CELL_FIELDTYPES[cell]
        if fieldtype in po_line
    }


def _injected(
    pool: Sequence[Seed], type_: DiscrepancyType, rng: random.Random, count: int
) -> list[Case]:
    """`count` cases each carrying one finding of the type, bands alternating.

    Each pass goes round the eligible seeds once in a fresh order. A pool with
    no eligible seed yields no case, so the type reads with n 0 rather than
    failing the report; a pass that builds nothing from eligible seeds means
    no seed can draw the band wanted, which is an error.
    """
    eligible_pool = [each for each in pool if eligible(each, type_)]
    cases: list[Case] = []
    if not eligible_pool:
        return cases
    while len(cases) < count:
        built = 0
        for seed in _cycle(eligible_pool, rng, len(eligible_pool)):
            if len(cases) == count:
                break
            wanted: Band = "near" if len(cases) % 2 == 0 else "far"
            case = _price_variance(seed, wanted, rng)
            if case is not None:
                cases.append(case)
                built += 1
        if not built:
            raise GeneratorError(
                f"no seed carries a line where a {wanted} {type_} can be drawn"
            )
    return cases


def _price_variance(seed: Seed, wanted: Band, rng: random.Random) -> Case | None:
    """The seed with one purchase-order line's price lowered into the band, or
    None when no line of it can be."""
    positions = list(eligible(seed, "price variance"))
    rng.shuffle(positions)
    for position in positions:
        line = seed.invoice.lines[position]
        priced = _priced(line)
        assert priced is not None
        cell, fieldtype, value = priced
        lowered = _lowered(PRICE, value, wanted, rng)
        if lowered is None:
            continue
        po_lines = list(seed.invoice.lines)
        po_lines[position] = {**line, fieldtype: (lowered,)}
        place = Place("po line", position)
        return _case(seed, po_lines, (Injected("price variance", place, wanted),))
    return None


def _priced(line: FieldValues) -> tuple[Cell, str, Decimal] | None:
    """The cell a price variance touches on this line, with the fieldtype it
    is under and the value the matcher will compare, the highest listed.

    The unit price where the line carries a readable one, else the amount,
    which is the matcher's own fallback: an unreadable unit price is skipped
    on both sides, a readable one is the cell compared, so a line whose
    readable unit price is not positive is not eligible at all.
    """
    for cell in ("unit price", "amount"):
        values = cell_values(line, cell)
        if values is None:
            continue
        numbers = [read_number(text) for text in values.texts]
        readable = [value for value in numbers if value is not None]
        if len(readable) < len(numbers):
            continue
        highest = max(readable)
        return (cell, values.fieldtype, highest) if highest > 0 else None
    return None


DRAWS = 100
"""How many values are drawn for a band before the line is given up on."""


def _lowered(
    tolerance: Tolerance, value: Decimal, wanted: Band, rng: random.Random
) -> str | None:
    """A purchase-order value below `value` by an overage in the band, written
    with the value's decimal places, or None when rounding leaves none."""
    low, high = (
        (tolerance.percent, NEAR_PERCENT)
        if wanted == "near"
        else (NEAR_PERCENT, FAR_PERCENT)
    )
    exponent = value.as_tuple().exponent
    places = Decimal(10) ** min(-2, exponent if isinstance(exponent, int) else 0)
    for _ in range(DRAWS):
        share = Decimal(rng.uniform(float(low), float(high)))
        lowered = (value / (1 + share)).quantize(places, rounding=ROUND_HALF_UP)
        if band(tolerance, value, lowered) == wanted:
            return f"{lowered:f}"
    return None
