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
the gate's business. The header is copied too, with one tax: a seed that
lists several keeps its highest, the document's total. The receiving record
copies quantities, units, codes and descriptions, never prices, and each of
its lines names the purchase-order line it was received against, as an ERP's
does (#63).

Cases
-----

A non-clean case carries one to three discrepancies, drawn at random, never
two on one line and at most one tax mismatch, so that pairing is tested
alongside the rules (#66). Each case starts from the type being filled, then
draws the rest from every type, one after another, each on a line nothing
else took; a type that cannot go on what is left is not drawn again for the
case, and a case with nothing left carries fewer. A type can come twice, on
two lines. The places are the ones the finished records give, after an extra
line has left the purchase order and a missing line has joined it.

Beside the truth every case carries its pairing key, which invoice line each
purchase-order line answers (#102). A purchase order copies its seed's lines,
so the generator knows the answer while it builds the case; writing it down
is what lets the scorer count a line paired with the wrong partner instead of
leaving it to show up as a finding somewhere else. It is the scorer's alone:
the matcher never sees a case, only the three records.

Injection
---------

Price variance lowers the purchase order's unit price where the seed line
carries one, else its amount, so the invoice ends up billed above the
purchase order (#65). Tax mismatch lowers the purchase order's header
`amount_total_tax`, header only, since no line tax is labeled (#65). Each
injection draws near the edge or far past it, half and half, in the bands
`tolerances` defines, so that borderline misses show in recall (#66, #70).
The lowered value is written with the seed value's own decimal places, and
redrawn until it lands in its band after rounding; a line where no value in
the band exists, which is a small value where the cent floor swallows the
band, is not eligible for that band.

Extra line removes a line from the purchase order and the receiving record,
so the invoice bills a line nobody ordered. Missing line adds to the purchase
order a labeled line from another seed in the same pool, with its code and
description, at a random position, so the unrelated line the pairing floor
was set on is the one it meets (#65, #77); a donor line with neither is not
given, since it would meet no floor. Neither has a band. A line alike
on every pairing cell, once normalized, to another line of the records
it would join or leave is never the one removed or added: the matcher could
not say which of the two was injected, so the truth could not be scored.
Nor is a line that carries no pairing cell, which the matcher does not
compare at all.

Short-ship lowers the receiving record's quantity below the invoice's, which
still equals the purchase order's, so the case bills goods not received.
Over-ship lowers the purchase order's quantity below both the invoice's and
the receipt's, which keeps the seed's, so more was shipped and billed than
was ordered (#65). Near is exactly one unit fewer, far two units fewer or
more, down to half (#70). A lowered quantity stays above zero, so a line of
one unit carries neither type, and one under four units has no far band.

Unit variant counts one purchase-order line in another unit, on a line that
labels one (#65). The purchase order's quantity is the invoice's times a
whole factor, its unit price the invoice's over that factor, and its amount
the seed's, so only the unit disagrees; with no conversion table the matcher
cannot tell how many of one unit make the other. A line whose quantity or
unit price the normalizer cannot read is not eligible, since it could not
be recounted. The receiving record copies
the purchase order's unit and quantity, as goods received against it would
be. It has no band.

Hard negatives
--------------

Every line no discrepancy took, in clean cases and discrepancy cases alike,
draws one hard negative or stays as labeled, evenly among what it can carry,
so that clean lines tempt the matcher and any finding on one is a false
alarm (#65, #66). Rounding drift moves the compared price cell, unit price
else amount, exactly one cent over or under. Just inside lowers it by 90 to
100 percent of the margin, never on a value under 1.00, where that is the
cent again, and never on a quantity, whose tolerance is exact; a line where
rounding leaves no value in that band stays as labeled. Billed below raises
the purchase order's compared price cell or its quantity, the receipt
following the quantity, so the invoice bills less than was ordered, which is
never a finding (#70). The truth records each one's kind, cell and place.

Lines with nothing else to pair on
----------------------------------

No discrepancy and no hard negative changes every cell a line pairs on. A
line with no code and no description pairs by its values alone, so lowering
its only price, or recounting its only quantity and unit price, would leave
it nothing in common with its own purchase-order line: the matcher could not
tell it from an unrelated one, and the truth could not be scored, the same
reason a line alike to another is never removed or added. Such a line still
carries what leaves one of its values as labeled.

Sizes and the pinned seed
-------------------------

At least `FINDINGS_PER_TYPE` injected findings per type and `CLEAN_CASES`
clean cases, from one random seed pinned in code, so that a rate near 0.95
reads to about plus or minus 2 points (#66). Seeds are reused with fresh
draws where a pool is smaller than the count. Every type fills its own count
with the cases it starts, so the discrepancies mixed cases add on top take
it past the count, a common type more than a rare one. Cases are rebuilt
from the labels on every run and never saved, so rerunning the command at
the linked commit reproduces the number and no document content is written
anywhere.

The draw is `random.Random` on Python 3.12, whose Mersenne Twister stream
CPython promises; `uv.lock` pins the interpreter the numbers were produced
with.
"""

import random
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, get_args

from docmatch.matching.matcher import (
    PAIRING_CELLS,
    DiscrepancyType,
    Place,
    named,
    pairable,
    pairing_cells,
)
from docmatch.matching.records import (
    CELL_FIELDTYPES,
    PRICE_CELLS,
    Cell,
    LineCell,
    ReceiptLine,
    ReceivingRecord,
    Record,
    cell_values,
    listed_units,
    read_cell,
)
from docmatch.matching.tolerances import (
    FAR_PERCENT,
    FAR_UNITS,
    JUST_INSIDE_LEAST,
    JUST_INSIDE_SHARE,
    NEAR_PERCENT,
    NEAR_UNITS,
    PRICE,
    TAX,
    Band,
    Tolerance,
    band,
    quantity_band,
)
from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import normalize_text, read_number

SEED = 20260919
"""The random seed every case is rebuilt from. Changing it draws new cases."""

CLEAN_CASES = 1000
FINDINGS_PER_TYPE = 500

MOST_PER_CASE = 3
"""The most discrepancies one case carries: each draws one up to this (#66)."""

BANDED_TYPES: tuple[DiscrepancyType, ...] = (
    "price variance",
    "short-ship",
    "over-ship",
    "tax mismatch",
)
"""The types whose injections draw near the edge or far past it: those with a
tolerance to be near. The others carry no band."""

HardNegativeKind = Literal["rounding drift", "just inside", "billed below"]
HARD_NEGATIVE_KINDS: tuple[HardNegativeKind, ...] = get_args(HardNegativeKind)
"""Every kind of hard negative, in the order a report lists them (#66)."""

RECEIPT_CELLS: tuple[LineCell, ...] = ("quantity", "unit", "code", "description")
"""What a receiving record copies from a purchase-order line: never prices."""

QUANTITY_FIELDTYPE = CELL_FIELDTYPES["quantity"][0]
"""Where a lowered quantity is written, on the purchase order or the receipt."""

UNIT_FIELDTYPE = CELL_FIELDTYPES["unit"][0]
"""Where a unit variant writes the purchase-order line's unit."""

PO_UNITS = ("EA", "BOX", "PACK", "CASE")
"""The units a unit variant counts a purchase-order line in, one unlike every
unit the seed line lists. Which one does not matter to a matcher that compares
units as text, with no conversion table (#65)."""

PO_UNITS_PER_INVOICE_UNIT = range(2, 13)
"""How many of a unit variant's purchase-order units make one of the
invoice's: a whole number, so a whole quantity stays whole."""

DRAWS = 100
"""How many values are drawn for a band before the line is given up on."""


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
    band: Band | None
    """None for a type with no tolerance to be near or far from."""


@dataclass(frozen=True)
class HardNegative:
    """A clean line built to tempt the matcher: any finding on it is a false alarm."""

    kind: HardNegativeKind
    place: Place
    """The purchase-order line it is on."""
    cell: Cell
    """The cell it moved on the purchase-order line."""
    invoice_line: int
    """The invoice line the purchase-order line copies, where an extra line
    for it would be placed."""


@dataclass(frozen=True)
class Case:
    """One seed with its derived records, either clean or carrying injections."""

    document_id: str
    invoice: Record
    purchase_order: Record
    receipt: ReceivingRecord
    truth: tuple[Injected, ...]
    pairing_key: tuple[int | None, ...]
    """Which invoice line each purchase-order line answers, one entry per
    purchase-order line, in order. Written here and read only by the scorer,
    never by the matcher, so pairing stays the matcher's own job and a wrong
    partner is counted rather than hidden. It is partial: a line a missing
    line added answers a line of another seed and reads None, and an invoice
    line an extra line removed is named by no entry (#102). Any length other
    than the purchase order's is a case that could not be scored, so it is
    refused here rather than read as a short key."""
    hard_negatives: tuple[HardNegative, ...] = ()

    def __post_init__(self) -> None:
        if len(self.pairing_key) != len(self.purchase_order.lines):
            raise ValueError(
                f"the pairing key names {len(self.pairing_key)} purchase-order "
                f"lines, the record has {len(self.purchase_order.lines)}"
            )

    @property
    def is_clean(self) -> bool:
        return not self.truth


def generate(
    seeds: Sequence[Seed],
    *,
    seed: int = SEED,
    clean: int = CLEAN_CASES,
    per_type: int = FINDINGS_PER_TYPE,
    most: int = MOST_PER_CASE,
) -> tuple[Case, ...]:
    """`clean` clean cases, then cases starting from each injected type until
    `per_type` of it, each carrying one to `most` discrepancies, all from one
    draw seeded with `seed`."""
    rng = random.Random(seed)
    pool = [each for each in seeds if each.invoice.lines]
    cases = [_tempted(_Draft(each, rng, pool)) for each in _cycle(pool, rng, clean)]
    mixed: Counter[DiscrepancyType] = Counter()
    for type_ in INJECTED_TYPES:
        cases += _injected(pool, type_, rng, per_type, most, mixed)
    return tuple(cases)


def eligible(
    seed: Seed, type_: DiscrepancyType, pool: Sequence[Seed]
) -> tuple[int, ...]:
    """The positions of the seed's lines a type can be injected on; for a
    missing line, every position the added line can go in, when another seed
    in the pool can give one; none for a header type."""
    return INJECTORS[type_].positions(seed, pool)


def carries(seed: Seed, type_: DiscrepancyType, pool: Sequence[Seed]) -> bool:
    """Whether a type can be injected on the seed at all: on its header for a
    header type, else on one of its lines."""
    injector = INJECTORS[type_]
    return injector.header(seed.invoice.header) or bool(injector.positions(seed, pool))


def documents_carrying(seeds: Sequence[Seed], type_: DiscrepancyType) -> int:
    """How many seeds a type can be injected on at all."""
    return sum(1 for each in seeds if carries(each, type_, seeds))


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


# Drafting a case


@dataclass(eq=False)
class _Slot:
    """One purchase-order line as it is being built: a copy of an invoice
    line, or a line another seed gave. Compared by identity, so that it can
    be found again after other lines are removed or added around it."""

    cells: FieldValues
    invoice_line: int | None
    """The invoice line it copies; None for a line another seed gave."""
    received: FieldValues | None = None
    """The receipt's cells when an injection set them apart; else the line
    is received in full, as it stands."""
    removed: bool = False


@dataclass(frozen=True)
class _Injection:
    """A discrepancy on a draft, before the finished records place it."""

    type: DiscrepancyType
    slot: _Slot | None
    """None for the header."""
    band: Band | None


@dataclass(frozen=True)
class _Tempted:
    """A hard negative on a draft, before the finished records place it."""

    kind: HardNegativeKind
    slot: _Slot
    cell: Cell


class _Draft:
    """A case being built from a seed: its purchase-order lines, its header,
    and what was put on them, placed only once every line has been removed
    or added. It carries the stream every injection draws from and the pool a
    donor line comes from, so an injector takes the draft and the band it is
    drawing for and nothing else."""

    def __init__(self, seed: Seed, rng: random.Random, pool: Sequence[Seed]) -> None:
        self.seed = seed
        self.rng = rng
        self.pool = pool
        self.slots = [
            _Slot(line, position) for position, line in enumerate(seed.invoice.lines)
        ]
        self.header = _one_tax(seed.invoice.header)
        self.injected: list[_Injection] = []
        self.negatives: list[_Tempted] = []

    def copied(self, position: int) -> _Slot:
        """The purchase-order line that copies this invoice line."""
        return next(each for each in self.slots if each.invoice_line == position)

    def free(self, positions: Collection[int] | None = None) -> list[int]:
        """The invoice lines no discrepancy is on, of those given or of all."""
        taken = {each.slot.invoice_line for each in self.injected if each.slot}
        every = range(len(self.seed.invoice.lines)) if positions is None else positions
        return [each for each in every if each not in taken]

    @property
    def taxed(self) -> bool:
        return any(each.type == "tax mismatch" for each in self.injected)

    def case(self) -> Case:
        """The finished records, and every injection and hard negative placed
        on the purchase-order line it ended up at."""
        live = [each for each in self.slots if not each.removed]
        at = {id(each): position for position, each in enumerate(live)}

        def po_line(slot: _Slot) -> Place:
            return Place("po line", at[id(slot)])

        def place(injection: _Injection) -> Place:
            if injection.slot is None:
                return Place("header")
            if injection.type == "extra line":
                return Place("invoice line", injection.slot.invoice_line)
            return po_line(injection.slot)

        return Case(
            document_id=self.seed.document_id,
            invoice=self.seed.invoice,
            purchase_order=Record(
                header=self.header, lines=tuple(each.cells for each in live)
            ),
            receipt=ReceivingRecord(
                tuple(
                    ReceiptLine(
                        cells=_received(each.cells)
                        if each.received is None
                        else each.received,
                        po_line=position,
                    )
                    for position, each in enumerate(live)
                )
            ),
            truth=tuple(
                Injected(each.type, place(each), each.band) for each in self.injected
            ),
            hard_negatives=tuple(
                HardNegative(each.kind, po_line(each.slot), each.cell, position)
                for each in self.negatives
                if (position := each.slot.invoice_line) is not None
            ),
            pairing_key=tuple(each.invoice_line for each in live),
        )


def _received(po_line: FieldValues) -> FieldValues:
    return {
        fieldtype: tuple(po_line[fieldtype])
        for cell in RECEIPT_CELLS
        for fieldtype in CELL_FIELDTYPES[cell]
        if fieldtype in po_line
    }


def _injected(
    pool: Sequence[Seed],
    type_: DiscrepancyType,
    rng: random.Random,
    count: int,
    most: int,
    mixed: Counter[DiscrepancyType],
) -> list[Case]:
    """`count` cases starting from one finding of the type, bands alternating
    where the type has them, each mixed with up to `most` less one more.

    Each pass goes round the eligible seeds once in a fresh order. A pool with
    no eligible seed yields no case, so the type reads with n 0 rather than
    failing the report; a pass that builds nothing from eligible seeds means
    no seed can draw the band wanted, which is an error.
    """
    eligible_pool = [each for each in pool if carries(each, type_, pool)]
    cases: list[Case] = []
    if not eligible_pool:
        return cases
    while len(cases) < count:
        built = 0
        for seed in _cycle(eligible_pool, rng, len(eligible_pool)):
            if len(cases) == count:
                break
            wanted: Band = "near" if len(cases) % 2 == 0 else "far"
            draft = _Draft(seed, rng, pool)
            if INJECTORS[type_].inject(draft, wanted):
                _mix(draft, rng.randint(1, most) - 1, mixed)
                cases.append(_tempted(draft))
                built += 1
        if not built:
            raise GeneratorError(
                f"no seed carries a line where a {wanted} {type_} can be drawn"
            )
    return cases


def _mix(draft: _Draft, more: int, mixed: Counter[DiscrepancyType]) -> None:
    """Up to `more` discrepancies drawn from every type onto what the draft
    has left. Each type's mixed-in findings alternate near and far on their
    own count, so the bands stay half and half whatever the case began with."""
    types = list(INJECTED_TYPES)
    while more and types:
        type_ = draft.rng.choice(types)
        wanted: Band = "near" if mixed[type_] % 2 == 0 else "far"
        if INJECTORS[type_].inject(draft, wanted):
            mixed[type_] += 1
            more -= 1
        else:
            types.remove(type_)


# Injection


def _price_variance(draft: _Draft, wanted: Band) -> bool:
    """One free purchase-order line's price lowered into the band, or False
    when no line of it can be."""
    lines = [
        (position, priced)
        for position in draft.free(eligible(draft.seed, "price variance", draft.pool))
        if (priced := _priced(draft.seed.invoice.lines[position])) is not None
    ]
    draft.rng.shuffle(lines)
    for position, priced in lines:
        lowered = _lowered(PRICE, priced.value, wanted, draft.rng)
        if lowered is None:
            continue
        slot = draft.copied(position)
        slot.cells = {**slot.cells, priced.fieldtype: (lowered,)}
        draft.injected.append(_Injection("price variance", slot, wanted))
        return True
    return False


def _extra_line(draft: _Draft, wanted: Band) -> bool:
    """One free line left off the purchase order and the receipt."""
    positions = draft.free(eligible(draft.seed, "extra line", draft.pool))
    if not positions:
        return False
    slot = draft.copied(draft.rng.choice(positions))
    slot.removed = True
    draft.injected.append(_Injection("extra line", slot, None))
    return True


def _missing_line(draft: _Draft, wanted: Band) -> bool:
    """A line from another seed added to the purchase order, at a random
    position, unlike every line already on it."""
    taken = {pairing_cells(each.cells) for each in draft.slots}
    for _ in range(DRAWS):
        given = [
            each
            for each in _lines_to_give(draft.seed, draft.rng.choice(draft.pool))
            if pairing_cells(each) not in taken
        ]
        if given:
            break
    else:
        return False
    slot = _Slot(draft.rng.choice(given), None)
    draft.slots.insert(draft.rng.randint(0, len(draft.slots)), slot)
    draft.injected.append(_Injection("missing line", slot, None))
    return True


def _lines_to_give(seed: Seed, donor: Seed) -> list[FieldValues]:
    """The donor's lines that carry a code or a description and are unlike
    every line of the seed; none when it is the seed. A line with values only
    would meet no floor, only the value tiebreak, so it is not given (#77)."""
    if donor.document_id == seed.document_id:
        return []
    seed_keys = {pairing_cells(line) for line in seed.invoice.lines}
    return [
        line
        for line in donor.invoice.lines
        if named(line) and pairing_cells(line) not in seed_keys
    ]


def _still_pairs(line: FieldValues, changed: Collection[Cell]) -> bool:
    """Whether the line keeps a pairing cell once `changed` are moved on
    its purchase-order copy: without one, nothing ties the two together."""
    return any(
        cell_values(line, cell) is not None
        for cell in PAIRING_CELLS
        if cell not in changed
    )


@dataclass(frozen=True)
class _Highest:
    """A cell read in full and the highest value it lists, which is the one
    the matcher compares, with the text it was written as and the fieldtype it
    sits under."""

    cell: Cell
    fieldtype: str
    text: str
    value: Decimal


def _highest(values: FieldValues, cell: Cell) -> _Highest | None:
    """The cell's highest value on this line or header; None when it carries
    none, or when the normalizer cannot read one of its texts, which leaves
    nothing an injection could be drawn from."""
    read = read_cell(values, cell)
    if read is None or read.numbers is None:
        return None
    at = read.numbers.index(max(read.numbers))
    return _Highest(cell, read.fieldtype, read.texts[at], read.numbers[at])


def _priced(line: FieldValues) -> _Highest | None:
    """The cell a price variance or a price hard negative moves on this line,
    and the value the matcher will compare: the first of `PRICE_CELLS` the
    line carries and the normalizer reads, which is the cell the matcher
    compares, since the purchase order copies the line. A line whose readable
    price is not positive is not eligible: lowering it would not overbill."""
    for cell in PRICE_CELLS:
        highest = _highest(line, cell)
        if highest is None:
            continue
        return highest if highest.value > 0 else None
    return None


def _fewer_on_one_line(type_: DiscrepancyType) -> "Inject":
    """One free line's quantity lowered into the band, or False when no line
    of it can be: a short-ship lowers the receipt's and leaves the purchase
    order as seeded, an over-ship lowers the purchase order's and leaves the
    line received in full."""

    def inject(draft: _Draft, wanted: Band) -> bool:
        lines = [
            (position, quantity)
            for position in draft.free(eligible(draft.seed, type_, draft.pool))
            if (quantity := _quantity(draft.seed.invoice.lines[position])) is not None
        ]
        draft.rng.shuffle(lines)
        for position, quantity in lines:
            fewer = _fewer(quantity, wanted, draft.rng)
            if fewer is None:
                continue
            slot = draft.copied(position)
            slot.received = _received(slot.cells)
            if type_ == "short-ship":
                slot.received = {**slot.received, QUANTITY_FIELDTYPE: (fewer,)}
            else:
                slot.cells = {**slot.cells, QUANTITY_FIELDTYPE: (fewer,)}
            draft.injected.append(_Injection(type_, slot, wanted))
            return True
        return False

    return inject


def _quantity(line: FieldValues) -> Decimal | None:
    """The quantity a short-ship or over-ship is lowered from, the highest
    listed, which is the one the matcher compares; None when the line
    carries none the normalizer reads, or one no lowering keeps above zero."""
    highest = _highest(line, "quantity")
    if highest is None or highest.value <= NEAR_UNITS:
        return None
    return highest.value


def _fewer(quantity: Decimal, wanted: Band, rng: random.Random) -> str | None:
    """A quantity below `quantity` by a whole number of units in the band, or
    None when the band holds none: far needs two units that are at most half."""
    if wanted == "near":
        fewer = quantity - NEAR_UNITS
    else:
        most = int(quantity / 2)
        if most < FAR_UNITS:
            return None
        fewer = quantity - rng.randint(int(FAR_UNITS), most)
    return f"{fewer:f}" if quantity_band(quantity, fewer) == wanted else None


def _tax_mismatch(draft: _Draft, wanted: Band) -> bool:
    """The purchase order's header tax lowered into the band, or False when it
    cannot be or already was."""
    taxed = _taxed(draft.seed.invoice.header)
    if taxed is None or draft.taxed:
        return False
    lowered = _lowered(TAX, taxed.value, wanted, draft.rng)
    if lowered is None:
        return False
    draft.header = {**draft.header, taxed.fieldtype: (lowered,)}
    draft.injected.append(_Injection("tax mismatch", None, wanted))
    return True


def _unit_variant(draft: _Draft, wanted: Band) -> bool:
    """One free purchase-order line counted in another unit, its quantities
    times a whole factor and its unit prices over it, the amount as seeded,
    and received in full in that unit."""
    positions = draft.free(eligible(draft.seed, "unit variant", draft.pool))
    if not positions:
        return False
    position = draft.rng.choice(positions)
    line = draft.seed.invoice.lines[position]
    counts = _counts(line)
    if counts is None:
        return False
    factor = Decimal(draft.rng.choice(PO_UNITS_PER_INVOICE_UNIT))
    slot = draft.copied(position)
    slot.cells = {
        **line,
        UNIT_FIELDTYPE: (draft.rng.choice(_other_units(line)),),
        **{
            fieldtype: tuple(_recount(fieldtype, each, factor) for each in numbers)
            for fieldtype, numbers in counts.items()
        },
    }
    draft.injected.append(_Injection("unit variant", slot, None))
    return True


RECOUNTED_CELLS: tuple[LineCell, ...] = ("quantity", "unit price")
"""What a unit variant moves on its purchase-order line."""

ORDERED_CELLS: tuple[LineCell, ...] = ("quantity",)
"""What an over-ship, or a quantity billed below, moves on its line."""


def _unit_varies(line: FieldValues) -> bool:
    """Whether a unit variant can be injected on the line: it labels a unit
    another can replace, it keeps a pairing cell once its counts are
    recounted, and its counts can be recounted."""
    return (
        bool(listed_units(line))
        and bool(_other_units(line))
        and _still_pairs(line, RECOUNTED_CELLS)
        and _counts(line) is not None
    )


def _other_units(line: FieldValues) -> list[str]:
    """The units a unit variant can give the line: unlike every unit it lists,
    by the rule the matcher compares units by."""
    listed = listed_units(line)
    return [each for each in PO_UNITS if normalize_text(each) not in listed]


def _counts(line: FieldValues) -> dict[str, tuple[Decimal, ...]] | None:
    """The line's quantity and its unit prices, gross and net, as numbers,
    each fieldtype it carries; None when the normalizer cannot read one in
    full, since a unit variant could not recount it and the purchase order's
    amount would stop agreeing with the rest."""
    counts: dict[str, tuple[Decimal, ...]] = {}
    for fieldtype in (QUANTITY_FIELDTYPE, *CELL_FIELDTYPES["unit price"]):
        texts = line.get(fieldtype, ())
        numbers = tuple(each for each in map(read_number, texts) if each is not None)
        if len(numbers) < len(texts):
            return None
        if numbers:
            counts[fieldtype] = numbers
    return counts


def _recount(fieldtype: str, value: Decimal, factor: Decimal) -> str:
    """A quantity times the factor, or a unit price over it, written with the
    price's decimal places and at least four, so a small one does not round
    to nothing."""
    if fieldtype == QUANTITY_FIELDTYPE:
        return f"{value * factor:f}"
    divided = (value / factor).quantize(_places(value, 4), rounding=ROUND_HALF_UP)
    return f"{divided:f}"


def _one_tax(header: FieldValues) -> FieldValues:
    """The header as a purchase order carries it: one tax, the highest the seed
    lists. Every seed on train and val that lists two taxes lists a zero beside
    its total, so the highest is the document's tax, and a purchase order
    copying both would fire against itself under the matcher's listed-values
    rule."""
    highest = _highest(header, "tax")
    if highest is None:
        return header
    return {**header, highest.fieldtype: (highest.text,)}


def _taxed(header: FieldValues) -> _Highest | None:
    """The header's tax as a tax mismatch lowers it; None when the header
    carries no readable positive tax, since lowering it would not overbill."""
    highest = _highest(header, "tax")
    return highest if highest is not None and highest.value > 0 else None


def _price_varies(line: FieldValues) -> bool:
    priced = _priced(line)
    return priced is not None and _still_pairs(line, (priced.cell,))


def _ships_fewer(line: FieldValues) -> bool:
    return _quantity(line) is not None


def _orders_fewer(line: FieldValues) -> bool:
    return _quantity(line) is not None and _still_pairs(line, ORDERED_CELLS)


def _taxes(header: FieldValues) -> bool:
    return _taxed(header) is not None


Inject = Callable[[_Draft, Band], bool]
"""How a type goes onto a draft, for the band wanted where the type has bands;
False when it cannot go on what the draft has left."""

Positions = Callable[[Seed, Sequence[Seed]], tuple[int, ...]]
"""Where on a seed a type can be injected: the positions of its invoice lines,
or for a missing line every position the added line can go in."""


def _on_lines(carried: Callable[[FieldValues], bool]) -> Positions:
    """Every position of the seed's lines that carries what the type touches,
    for the types whose eligibility is the line's own. A short-ship moves only
    the receipt, so it leaves pairing, which is invoice to purchase order, as
    labeled."""

    def positions(seed: Seed, pool: Sequence[Seed]) -> tuple[int, ...]:
        return tuple(
            position
            for position, line in enumerate(seed.invoice.lines)
            if carried(line)
        )

    return positions


def _unlike_every_other(seed: Seed, pool: Sequence[Seed]) -> tuple[int, ...]:
    """Where an extra line can be taken from: a line carrying a pairing cell
    that is unlike every other line of the seed, so the matcher could say
    which one was removed."""
    lines = seed.invoice.lines
    keys = [pairing_cells(line) for line in lines]
    return tuple(
        position
        for position, key in enumerate(keys)
        if pairable(lines[position]) and keys.count(key) == 1
    )


def _anywhere_with_a_donor(seed: Seed, pool: Sequence[Seed]) -> tuple[int, ...]:
    """Where a missing line can be added: any position, when another seed in
    the pool can give a line, and none when none can."""
    has_donor = any(_lines_to_give(seed, each) for each in pool)
    return tuple(range(len(seed.invoice.lines) + 1)) if has_donor else ()


def _no_lines(seed: Seed, pool: Sequence[Seed]) -> tuple[int, ...]:
    """A header type goes on no line."""
    return ()


def _no_header(header: FieldValues) -> bool:
    """A line type touches no header cell."""
    return False


@dataclass(frozen=True)
class _Injector:
    """Everything the generator knows about one injected type: where it can
    go, and how it goes there."""

    inject: Inject
    """How it goes onto a draft."""
    positions: Positions = _no_lines
    """Where on the seed's lines it can go."""
    header: Callable[[FieldValues], bool] = _no_header
    """Whether the seed's header carries what it touches."""


INJECTORS: dict[DiscrepancyType, _Injector] = {
    "price variance": _Injector(_price_variance, _on_lines(_price_varies)),
    "extra line": _Injector(_extra_line, _unlike_every_other),
    "missing line": _Injector(_missing_line, _anywhere_with_a_donor),
    "short-ship": _Injector(_fewer_on_one_line("short-ship"), _on_lines(_ships_fewer)),
    "over-ship": _Injector(_fewer_on_one_line("over-ship"), _on_lines(_orders_fewer)),
    "tax mismatch": _Injector(_tax_mismatch, header=_taxes),
    "unit variant": _Injector(_unit_variant, _on_lines(_unit_varies)),
}
"""Every type the generator injects, in the order their cases are built. A new
type goes last, so the cases every other type draws stay the same. Tax mismatch
is the one header type: it goes on header `amount_total_tax` and on no line
(#65)."""

INJECTED_TYPES: tuple[DiscrepancyType, ...] = tuple(INJECTORS)
"""The types the generator injects, in that order."""


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
    places = _places(value, 2)
    for _ in range(DRAWS):
        share = Decimal(rng.uniform(float(low), float(high)))
        lowered = (value / (1 + share)).quantize(places, rounding=ROUND_HALF_UP)
        if band(tolerance, value, lowered) == wanted:
            return f"{lowered:f}"
    return None


def _places(value: Decimal, fewest: int) -> Decimal:
    """The quantum that writes a value with its own decimal places, and with
    at least `fewest`."""
    exponent = value.as_tuple().exponent
    return Decimal(10) ** min(-fewest, exponent if isinstance(exponent, int) else 0)


# Hard negatives


def _tempted(draft: _Draft) -> Case:
    """The draft with a hard negative drawn on every line no discrepancy is
    on, or the line left as labeled, evenly among what it can carry."""
    rng = draft.rng
    for position in draft.free():
        slot = draft.copied(position)
        if slot.removed:
            continue
        line = draft.seed.invoice.lines[position]
        tempting = _tempting(line)
        kind = rng.choice([None, *dict.fromkeys(kind for kind, _ in tempting)])
        if kind is None:
            continue
        cell = rng.choice([cell for each, cell in tempting if each == kind])
        highest = _highest(line, cell)
        assert highest is not None
        written = TEMPT[kind](highest, rng)
        if written is None:
            continue
        slot.cells = {**slot.cells, highest.fieldtype: (written,)}
        draft.negatives.append(_Tempted(kind, slot, cell))
    return draft.case()


def _tempting(line: FieldValues) -> list[tuple[HardNegativeKind, Cell]]:
    """Every hard negative the line can carry, and the cell each would move:
    the compared price cell for all three kinds, the quantity for billed below
    only, each where the line still pairs once it moves (#70)."""
    tempting: list[tuple[HardNegativeKind, Cell]] = []
    priced = _priced(line)
    if priced is not None and _still_pairs(line, (priced.cell,)):
        tempting.append(("rounding drift", priced.cell))
        if priced.value >= JUST_INSIDE_LEAST:
            tempting.append(("just inside", priced.cell))
        tempting.append(("billed below", priced.cell))
    quantity = _highest(line, "quantity")
    if (
        quantity is not None
        and quantity.value > 0
        and _still_pairs(line, ORDERED_CELLS)
    ):
        tempting.append(("billed below", "quantity"))
    return tempting


def _drift(highest: _Highest, rng: random.Random) -> str:
    """The value one cent over or under, as the purchase order's: the invoice
    then bills a cent more or less. Under when over would leave nothing."""
    value = highest.value
    places = _places(value, 2)
    bills_over = rng.random() < 0.5 and value > PRICE.cent
    moved = value - PRICE.cent if bills_over else value + PRICE.cent
    return f"{moved.quantize(places, rounding=ROUND_HALF_UP):f}"


def _just_inside(highest: _Highest, rng: random.Random) -> str | None:
    """A purchase-order value the invoice's is above by 90 to 100 percent of
    the margin, written with the value's decimal places, or None when
    rounding leaves none in that band."""
    value = highest.value
    places = _places(value, 2)
    for _ in range(DRAWS):
        share = Decimal(rng.uniform(float(JUST_INSIDE_SHARE), 1.0)) * PRICE.percent
        lowered = (value / (1 + share)).quantize(places, rounding=ROUND_HALF_UP)
        margin = PRICE.margin(lowered)
        if JUST_INSIDE_SHARE * margin <= value - lowered <= margin:
            return f"{lowered:f}"
    return None


def _billed_below(highest: _Highest, rng: random.Random) -> str | None:
    """A purchase-order price above the invoice's by past the tolerance up to
    half again, or a quantity above by one unit up to double; the invoice then
    bills less than was ordered."""
    value = highest.value
    if highest.cell == "quantity":
        return f"{value + rng.randint(1, max(1, int(value))):f}"
    share = Decimal(rng.uniform(float(PRICE.percent), float(FAR_PERCENT)))
    raised = (value * (1 + share)).quantize(_places(value, 2), rounding=ROUND_HALF_UP)
    return f"{raised:f}" if raised > value else None


TEMPT: dict[HardNegativeKind, Callable[[_Highest, random.Random], str | None]] = {
    "rounding drift": _drift,
    "just inside": _just_inside,
    "billed below": _billed_below,
}
"""How each kind of hard negative moves the value its cell compares. Only
billed below reads the cell, to tell a quantity from a price; the others move
the compared price cell whichever it is."""
