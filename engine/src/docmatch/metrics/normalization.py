"""Normalizing field values before a prediction is compared to a label.

A prediction and a label are two readings of the same ink, so they disagree
about formatting far more often than about content. `US$ 2,460.00` and `2460`
are one amount; `SEP18/26` and `09/18/2026` are one day. Scoring those as
misses would measure formatting rather than extraction.

Every value spelled out below is constructed to carry a formatting pattern the
annotated set contains. None of them is a label: DocILE may not be
redistributed, so only counts derived from it are written down here.

Each value goes through the one rule its fieldtype selects. A rule that cannot
read a value falls back to the text rule instead of guessing, so an unreadable
value still matches an identically unreadable one and never matches a different
one.

Why not DocILE's own metric
---------------------------

DocILE matches a predicted field to a labeled one by intersection over union of
the Pseudo-Character-Centers the two boxes cover, at a threshold of 1.0, per
fieldtype and per page; comparing the text is an option on top of that
(`docile/evaluation/pcc_field_matching.py` at rossumai/docile 45f1f00d1846, read
2026-09-11). That metric needs a predicted bounding box. The backends this
engine benchmarks return structured text and no boxes, so there is nothing to
compute an IOU over, and the score here is over normalized text instead. The
two numbers are therefore not comparable with the DocILE leaderboard, which is
fine: the benchmark that has to stay comparable is this repository's own,
across commits.

The rules
---------

**Text**, the default and the fallback for every other rule. Unicode NFKC, then
whitespace runs collapsed to one space, stripped, and casefolded. Field text
carries line breaks, so `Testville,\\nEX` and `Testville, EX` are one value.

**Number**, for the `amount_*` and `tax_detail_*` fieldtypes. A currency symbol
or code on either side is dropped, as is a trailing `%`, so `7.25%` and `7.25`
agree. A minus counts wherever it is written, which covers `$-1,234.50`,
`-$34.20 USD`, and the accounting `340.00-`. Which separator is the decimal
point is decided from the digits, not from a locale:

- Both `.` and `,` present: whichever appears last is the decimal point and the
  other groups thousands. `2,460.00` and `2.460,00` are both 2460.
- One kind, appearing more than once: it groups thousands when every group
  after the first is exactly three digits (`1.234.567`), otherwise the last one
  is the decimal point (`$204,806,35` is 204806.35).
- One kind, appearing once: `,` with exactly three digits after it groups
  thousands (`$2,500`), and everything else is a decimal point.

A lone `.` is therefore always a decimal point. That is measured, not assumed:
across the 5,680 annotated documents, 23 amount values are European thousands
written with a lone dot, the way `123.456` means 123456, against 74 tax rates
of the shape `7.250%`, which a thousands reading would inflate by a thousand.
Reading the rate right is worth the 23.

**Date**, for `date_issue` and `date_due`, canonicalized to `YYYY-MM-DD`.
Ordinal suffixes are dropped and a two-digit year pivots at 69, so `99` is 1999
and `20` is 2020. An all-numeric date that does not start with a four-digit
year is read month first: in this corpus 3,338 numeric dates prove month-first
ordering against 15 that prove day-first, so day-first is the rarer mislabel
and not a locale worth detecting. A date the rule cannot read falls back to
text rather than becoming the wrong day, which is what happens to a day-first
date whose day is past the twelfth.

A word names a month when it is at least three letters and starts exactly one
English month name, so `SEP`, `SEPT` and `MARCH` are months while `MAYBE` and
the ambiguous `JU` are not. That costs one label in the annotated set, a German
`JUNI`, and is worth it: matching on the first three letters alone would read
any word beginning `MAY` or `DEC` as a month.

**Currency**, for `currency_code_amount_due` and `line_item_currency`,
canonicalized to its ISO 4217 code. `$` is read as USD, which is true of this
corpus and would not be true of one carrying Canadian or Australian documents.

Line-item fieldtypes
--------------------

A line item's cells are fields like any other and take the same rules, so the
sets above name LIR fieldtypes beside the KILE ones. Which rule each cell gets
is decided by what its values are, measured across the 38,678 labeled rows of
the annotated set:

- The money and count cells take the number rule, which reads 97.9% of
  `line_item_amount_gross`, 99.3% of `line_item_unit_price_gross` and 98.9% of
  `line_item_quantity`. `line_item_position` is here too, at 99.1%, so that a
  row printed `01.` and a row printed `1` are one position.
- `line_item_tax_rate` takes it as well, although the rule reads only 13.5% of
  its 96 cells: the rest are a single letter, a tax code mislabeled as a rate,
  and the rule hands those to the text rule untouched.
- `line_item_date` takes the date rule, which reads 78.8%. What it does not
  read is a date missing its year (`06/21`), a period written as two dates, or
  a weekday, all of which stay text rather than become the wrong day.
- `line_item_code`, `line_item_order_id` and `line_item_hts_number` stay text
  even though the number rule can read 63.9%, 79.9% and 100% of them. They are
  identifiers, so their leading zeros and their dots are content: reading
  `8471.30.0100` as a number would make it agree with `8471.300100`.
- `line_item_description`, `line_item_person_name` and
  `line_item_units_of_measure` are text, and `line_item_currency` takes the
  currency rule, which reads 97.3% of it.
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

DATE_FIELDTYPES = frozenset({"date_issue", "date_due", "line_item_date"})
NUMBER_FIELDTYPES = frozenset(
    {
        "amount_due",
        "amount_paid",
        "amount_total_gross",
        "amount_total_net",
        "amount_total_tax",
        "tax_detail_gross",
        "tax_detail_net",
        "tax_detail_rate",
        "tax_detail_tax",
        "line_item_amount_gross",
        "line_item_amount_net",
        "line_item_discount_amount",
        "line_item_discount_rate",
        "line_item_position",
        "line_item_quantity",
        "line_item_tax",
        "line_item_tax_rate",
        "line_item_unit_price_gross",
        "line_item_unit_price_net",
        "line_item_weight",
    }
)
CURRENCY_FIELDTYPES = frozenset({"currency_code_amount_due", "line_item_currency"})


def normalize(fieldtype: str, text: str) -> str:
    """The comparable form of one field value, by the rule its fieldtype picks."""
    if fieldtype in NUMBER_FIELDTYPES:
        return normalize_number(text)
    if fieldtype in DATE_FIELDTYPES:
        return normalize_date(text)
    if fieldtype in CURRENCY_FIELDTYPES:
        return normalize_currency(text)
    return normalize_text(text)


def normalize_text(text: str) -> str:
    """Whitespace collapsed and case dropped; the fallback for every rule."""
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


_NUMBER = re.compile(
    r"""\A
    (?P<lead>[^\d.,]*)         # a currency symbol or code, and maybe the sign
    (?P<body>[\d.,]*\d[.,]?)
    (?P<trail>[^\d.,]*)        # a code, a %, or an accounting minus
    \Z""",
    re.VERBOSE,
)


def normalize_number(text: str) -> str:
    """An amount or rate as a plain decimal string, or the text rule if unreadable."""
    match = _NUMBER.match("".join(unicodedata.normalize("NFKC", text).split()))
    if match is None:
        return normalize_text(text)
    value = _read_separators(match["body"].rstrip(".,"))
    if value is None:
        return normalize_text(text)
    if "-" in match["lead"] + match["trail"]:
        value = -value
    return f"{value.normalize():f}"


def _read_separators(body: str) -> Decimal | None:
    """The digits as a number, deciding which separator is the decimal point."""
    kinds = {character for character in body if character in ".,"}
    try:
        if not kinds:
            return Decimal(body)
        if len(kinds) == 2:
            return _split_at(body, decimal_point=max(".,", key=body.rfind))
        separator = kinds.pop()
        groups = body.split(separator)
        thousands = all(len(group) == 3 for group in groups[1:]) and (
            separator == "," or len(groups) > 2
        )
        return Decimal("".join(groups)) if thousands else _split_at(body, separator)
    except InvalidOperation:
        return None


def _split_at(body: str, decimal_point: str) -> Decimal:
    """`body` read with `decimal_point` as the point and everything else dropped."""
    whole, _, fraction = body.rpartition(decimal_point)
    digits = re.sub(r"[.,]", "", whole)
    return Decimal(f"{digits or '0'}.{fraction or '0'}")


_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "JANUARY",
            "FEBRUARY",
            "MARCH",
            "APRIL",
            "MAY",
            "JUNE",
            "JULY",
            "AUGUST",
            "SEPTEMBER",
            "OCTOBER",
            "NOVEMBER",
            "DECEMBER",
        ),
        start=1,
    )
}
"""The month names a word has to start, so that `MAYBE` is not May."""
_ORDINAL = re.compile(r"(?<=\d)(?:ST|ND|RD|TH)")
_YEAR_PIVOT = 69
"""Two-digit years below this are 2000s, the rest 1900s, as `datetime` does it."""


def normalize_date(text: str) -> str:
    """A date as `YYYY-MM-DD`, or the text rule if it is not one calendar day."""
    day = _read_date(_ORDINAL.sub("", unicodedata.normalize("NFKC", text).upper()))
    return day.isoformat() if day is not None else normalize_text(text)


def _read_date(text: str) -> date | None:
    month: int | None = None
    for word in re.findall(r"[A-Z]+", text):
        named = _month(word)
        if named is None or month is not None:
            return None  # a word that is not a month, or a second month name
        month = named
        text = text.replace(word, " ", 1)
    numbers = [int(number) for number in re.findall(r"\d+", text)]
    if month is not None:
        if len(numbers) != 2:
            return None
        year, day = _year_and_day(numbers)
    elif len(numbers) != 3:
        return None
    elif len(str(numbers[0])) == 4:
        year, month, day = numbers  # an ISO date, the one unambiguous ordering
    else:
        month, day, year = numbers  # month first, as this corpus writes them
    try:
        return date(_four_digit(year), month, day)
    except ValueError:
        return None


def _month(word: str) -> int | None:
    """The month a word names, by the abbreviation it spells: `SEP`, `SEPT`, `MARCH`."""
    if len(word) < 3:
        return None
    named = [number for name, number in _MONTHS.items() if name.startswith(word)]
    return named[0] if len(named) == 1 else None


def _year_and_day(numbers: list[int]) -> tuple[int, int]:
    """Of the two numbers beside a month name, which is the year and which the day."""
    first, second = numbers
    if len(str(first)) == 4:
        return first, second
    return second, first


def _four_digit(year: int) -> int:
    if year >= 100:
        return year
    return year + (2000 if year < _YEAR_PIVOT else 1900)


_CURRENCIES = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "US DOLLARS": "USD",
    "U.S. DOLLARS": "USD",
    "£": "GBP",
    "GBP": "GBP",
    "€": "EUR",
    "EUR": "EUR",
    "¥": "JPY",
    "YEN": "JPY",
    "CHF": "CHF",
}


def normalize_currency(text: str) -> str:
    """A currency as its ISO 4217 code, or the text rule if it is not one we know."""
    code = _CURRENCIES.get(normalize_text(text).upper())
    return code.casefold() if code is not None else normalize_text(text)
