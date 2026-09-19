"""The constants matching decides by: tolerances, bands, and pairing floors.

One place holds them, as constants and not as flags or a config file, so that
a tolerance change is a commit that runs the eval and every README number
corresponds to one set (#70). Every finding records the constant it applied,
so an explanation can quote it.

Tolerances
----------

A price, and the header tax, may go over the purchase order by 1 percent of
the purchase order's value, and always by a cent: a finding needs an overage
above both. The percent scales with the value (line amounts run from 8.75 at
the tenth percentile to 3,719.44 at the ninetieth, median 104.00) and the
cent absorbs rounding on small values, where 1 percent is below a cent. Only
disagreement against the buyer counts: billing below never fires (#65).

The worked example: purchase order 35.30, so the margin is 0.353. An invoice
at 35.31 or 35.65 passes; 35.66 is a price variance.

Quantities are exact, since 17,477 of 17,975 labeled quantities are whole
numbers and seeds copy quantities rather than compute them (#70): the
quantity tolerance is a percent and a cent of zero.

Units are exact too, compared as text after the text normalization: with no
conversion table there is no way for two different units to agree (#65).

Bands
-----

An injected overage is near the edge when it is past the tolerance and at
most 2 percent of the purchase order's value (35.66 to 36.00 in the example),
and far when it is past 2 percent and at most 50 percent (up to 52.95). The
generator draws each half and half, so a headline recall is that mix and the
report splits it (#66, #70).

On quantities, which have no percent to scale with, near is exactly one unit
over and far is two units or more, up to double (#70).

Hard negatives sit on the clean side of the same edges. Rounding drift is
exactly the cent, over or under. Just inside is an overage of 90 to 100
percent of the margin (35.62 to 35.65 in the example), on values of 1.00 and
more only, and never on quantities, whose tolerance is exact (#70).

Pairing floors
--------------

The lowest similarity at which a code, or a description, counts as agreement
in pairing. Each is what the procedure in `floors` gives on the whole of
train, 10,000 pairs of lines from different documents per cell (#77): at 0.5,
0.76 percent of code pairs clear, against 1.69 percent at 0.4; at 0.4, 0.49
percent of description pairs clear, against 1.59 percent at 0.3. `docmatch
match` prints the procedure's value beside each, so a floor that no longer
follows its rule shows in every report.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Tolerance:
    """How far above the purchase order a money cell may go: a percent, and a cent."""

    percent: Decimal
    """Of the purchase order's value."""
    cent: Decimal
    """The overage must clear this too, whatever the percent comes to."""

    @property
    def exact(self) -> bool:
        """Whether any overage at all exceeds it."""
        return not self.percent and not self.cent

    def margin(self, po_value: Decimal) -> Decimal:
        """The percent of this purchase-order value, in money."""
        return self.percent * po_value

    def exceeded(self, invoice_value: Decimal, po_value: Decimal) -> bool:
        """Whether the invoice is over the purchase order by more than both."""
        overage = invoice_value - po_value
        return overage > self.margin(po_value) and overage > self.cent


PRICE = Tolerance(percent=Decimal("0.01"), cent=Decimal("0.01"))
"""On a line's unit price, else its amount."""

QUANTITY = Tolerance(percent=Decimal(0), cent=Decimal(0))
"""On a line's quantity, against the receipt and the purchase order: exact,
so any overage at all is over."""

TAX = Tolerance(percent=Decimal("0.01"), cent=Decimal("0.01"))
"""On the header's total tax: the same shape as price, a constant of its own so
a finding names which one it applied (#65, #70)."""

UNIT = Tolerance(percent=Decimal(0), cent=Decimal(0))
"""On a line's unit of measure, compared as text after the text normalization:
exact, since there is no conversion table to say two units agree (#65). A
constant of its own, like tax, so a finding names which one it applied."""

NEAR_PERCENT = Decimal("0.02")
"""An overage past the tolerance and at most this share of the PO value is near."""

FAR_PERCENT = Decimal("0.50")
"""An overage past the near band and at most this share of the PO value is far."""

Band = Literal["near", "far"]


def band(
    tolerance: Tolerance, invoice_value: Decimal, po_value: Decimal
) -> Band | None:
    """Which band an overage falls in, or None when it is within the tolerance
    or past the far band, where nothing is generated."""
    if not tolerance.exceeded(invoice_value, po_value):
        return None
    overage = invoice_value - po_value
    if overage <= NEAR_PERCENT * po_value:
        return "near"
    if overage <= FAR_PERCENT * po_value:
        return "far"
    return None


JUST_INSIDE_SHARE = Decimal("0.90")
"""A just-inside hard negative's overage is at least this share of the margin,
and at most all of it: 35.62 to 35.65 against a purchase order of 35.30 (#70)."""

JUST_INSIDE_LEAST = Decimal("1.00")
"""No just-inside hard negative on a value under this: 1 percent of it is below
the cent, so just inside would be rounding drift under another name (#70)."""


NEAR_UNITS = Decimal(1)
"""A quantity overage of exactly this many units is near the edge."""

FAR_UNITS = Decimal(2)
"""A quantity overage of at least this many units is far, up to double."""


def quantity_band(invoice_value: Decimal, lowered_value: Decimal) -> Band | None:
    """Which band a quantity overage falls in, or None when there is none or
    it is in neither band: a fraction of a unit, or past double, where nothing
    is generated."""
    overage = invoice_value - lowered_value
    if overage == NEAR_UNITS:
        return "near"
    if overage >= FAR_UNITS and invoice_value <= 2 * lowered_value:
        return "far"
    return None


CODE_FLOOR = 0.5
DESCRIPTION_FLOOR = 0.4
