"""Values code adds beside a reading, taken from what the reading already says.

A derived value never looks at the page again and never asks a model. It is
kept apart from the reading, tagged with the field it was copied from, and it
carries no confidence, so the saved reading stays exactly what the backend
returned and a report can show a field both as read and with derived values.
Derived values are computed from a saved run, so changing a rule here re-scores
saved predictions without paying for a new reading.

The currency of the amount due
------------------------------

The one rule so far. On the Phase 0 baseline's saved predictions the model left
`currency_code_amount_due` empty on 67 of the 71 documents that label it, while
the currency was printed inside a predicted amount on 55 of them (#24). It is a
recall miss, not a format miss: the scorer already reads `$` as USD.

So every currency symbol or code printed in the reading's `amount_due` and
`amount_total_gross` is added to `currency_code_amount_due`, beside whatever
the backend listed. Those two fields because that is where DocILE draws the
label: of that subset's 71 currency labels, 66 sit inside an amount box, 43
of them in `amount_total_gross` and 22 in `amount_due`.

A currency counts only when `normalization.CURRENCIES` knows it, and only as a
whole word, so `USDA` is not `USD` and the `$` of `AU$` is not a dollar the
scorer would read as USD. What is found is copied verbatim: resolving `$` to a
code is an ambiguous decision, and only the scorer makes it.

A vendor that returns an amount's currency apart from its text, as Azure's
prebuilt invoice model does with `currency_symbol` on `AmountDue` and
`InvoiceTotal`, is a second source for the same rule (#24): each symbol is
added under the amount it came with, on the same terms, and never its
`currency_code`, which would be the vendor resolving `$` to a code.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from docmatch.metrics.fields import FieldValues, Prediction
from docmatch.metrics.normalization import CURRENCIES, reads_currency

CURRENCY_FIELDTYPE = "currency_code_amount_due"
CURRENCY_SOURCES = ("amount_due", "amount_total_gross")

DERIVED_FIELDTYPES = (CURRENCY_FIELDTYPE,)
"""The fieldtypes a rule here adds values to, in the order a report lists them."""


@dataclass(frozen=True)
class DerivedValue:
    """One value code added to a fieldtype, and the field it was copied from."""

    fieldtype: str
    text: str
    source: str


_LETTER = r"[^\W\d_]"
_CURRENCY_PATTERN = re.compile(
    rf"(?<!{_LETTER})(?:"
    + "|".join(
        re.escape(name).replace(r"\ ", r"\s+")
        for name in sorted(CURRENCIES, key=len, reverse=True)
    )
    + rf")(?!{_LETTER})",
    re.IGNORECASE,
)
"""A currency the scorer knows, with no letter on either side of it.

Longest names first, so `US$ 118.00` yields `US$` rather than `$`. A digit or a
space beside it is fine: `118.00USD` prints a currency, `USDA` does not.
"""


def derive(
    prediction: Prediction,
    currency_symbols: Mapping[str, Sequence[str]] | None = None,
) -> tuple[DerivedValue, ...]:
    """Every derived value for one reading, in source then document order.

    `currency_symbols` are what the vendor returned beside each amount, keyed
    by the amount's fieldtype; they follow the ones printed in that amount.
    """
    header = prediction.header
    returned = currency_symbols or {}
    found: dict[DerivedValue, None] = {}
    for source in CURRENCY_SOURCES:
        for text in header.get(source, ()):
            for match in _CURRENCY_PATTERN.finditer(text):
                # The pattern allows any whitespace inside a name; the scorer
                # collapses it, and this asks the scorer rather than assuming.
                text_found = match.group()
                if reads_currency(text_found):
                    found[DerivedValue(CURRENCY_FIELDTYPE, text_found, source)] = None
        for symbol in returned.get(source, ()):
            if reads_currency(symbol):
                found[DerivedValue(CURRENCY_FIELDTYPE, symbol, source)] = None
    return tuple(found)


def with_derived(
    header: FieldValues, derived: Sequence[DerivedValue]
) -> dict[str, tuple[str, ...]]:
    """A reading's header with its derived values listed beside what was read."""
    combined = {fieldtype: tuple(values) for fieldtype, values in header.items()}
    for each in derived:
        combined[each.fieldtype] = (*combined.get(each.fieldtype, ()), each.text)
    return combined
