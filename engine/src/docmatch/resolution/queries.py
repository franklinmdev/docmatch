"""The query set: what every arm is asked, with the right answer beside it.

A query is one normalized description and nothing else, carrying the SKU of
the entry it was generated from as its truth (#119). Every entry carries
three: one exact query, its canonical description unchanged, and two noisy
variants that imitate how a backend's reading differs from the label. The
exact reading is about two readings in three (#116), and that share rides
as the exact weight in `run` rather than as duplicated queries, since an
entry has one exact form.

The noise model
---------------

Four kinds, the whole of what #116 found worth imitating, at its shares
renormalized over the mass they carry: the reading carries extra words
(35.0 percent), a letter or two substituted so that neither side contains
the other (24.5), digit-bearing words dropped (21.2), punctuation (19.2).
A variant draws one kind and keeps it, since a backend's error is a habit
of a document rather than a rate per row, and an entry's two variants draw
independently, two variants standing for two documents. A draw the model
rejects, a text that is another entry's description, redraws the strength
and never the kind, so a kind that collides more often does not lose share
to the others.

Not every entry can take every kind. Only an entry with a digit-bearing word
beside a plain one, whose digits dropped leave a text that is no entry, can
drop digits, about two entries in five on the real catalog, and an entry
with no letter cannot have one substituted. A variant therefore draws
whether it drops digits first, at the rate that makes the kind's share over
every variant land on its target given how many entries can take it, and
otherwise draws among the kinds its entry can take at their target
proportions. Each draw is still one independent draw per variant; the
conditioning is what keeps the shares honest on a catalog where the kind
that matters most, since 115 of #117's 287 near-duplicate pairs differ only
in their digits, would otherwise fall to a third of its measured share.

Each kind's strength is calibrated so that the variants' normalized
Levenshtein to their entry reproduces #116's bimodal shape: a reading is
either close to its label or a long way off, with little between. Letters
and punctuation are one edit or two, so they sit in the top band on all but
the shortest entries; digits dropped is one word or every digit-bearing
word, the middle bands; extra words is text longer than most entries, the
bottom band. The variant is normalized before it is a query, so spacing
alone never survives and the fourth kind is punctuation: a mark added,
swapped or dropped outright, the text the same once every mark is removed,
which is how #116 told punctuation from spacing.

The extra text never comes from a catalog entry (#119): for half the
variants it is a run of numeric or unit-shaped tokens standing for the
quantity, price, amount and unit cells of a row bleeding into the
description, appended after it as right-hand columns would; for the other
half a fragment of a train singleton description standing for page text, at
either end. Growth runs a median of about 26 characters on the real catalog,
inside #116's 14 to 32 per backend, and long enough that the bottom band
is mostly this kind's, as it was in the measurement. Appending an entry's
text would manufacture the merged reading carrying two entries that #116
ruled out, so added text that carries any entry's whole description,
word-bounded, is redrawn, as is any variant whose text equals another
entry's description.

The split
---------

The entries split into a scored slice and a development slice, one entry in
five to development, under one pinned seed, the catalog whole in both: every
knob is set on the development slice and the table is measured once on the
scored slice, so nothing is fitted on what the README shows. The same seed
gives the same split and the same variants; the out-of-catalog draw joins
the stream in #132.

The draw is `random.Random` on Python 3.12, whose Mersenne Twister stream
CPython promises; `uv.lock` pins the interpreter, as it does for the
generator's cases (#62).
"""

import random
import string
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from docmatch.matching.similarity import similarity
from docmatch.metrics.normalization import normalize_text
from docmatch.metrics.score import share_of
from docmatch.resolution.catalog import Catalog, Entry, ResolutionError

SEED = 20260921
"""The one seed the split, the kind draw and the strength draw come from,
pinned before the first draw; the number is arbitrary."""

VARIANTS = 2
"""Noisy variants per entry."""

DEVELOPMENT_ONE_IN = 5
"""One entry in this many goes to the development slice, the integer part."""

NoiseKind = Literal[
    "extra words", "letters substituted", "digits dropped", "punctuation"
]
"""The four ways a variant differs from its entry (#116)."""

NOISE_KINDS: tuple[NoiseKind, ...] = get_args(NoiseKind)

QueryKind = Literal["exact"] | NoiseKind
"""How a query was made from its entry: unchanged, or by one noise kind."""

QUERY_KINDS: tuple[QueryKind, ...] = ("exact", *NOISE_KINDS)
"""Every kind, in the order the report lists them."""

KIND_SHARES: dict[NoiseKind, float] = {
    "extra words": 0.350,
    "letters substituted": 0.245,
    "digits dropped": 0.212,
    "punctuation": 0.192,
}
"""Each kind's target share of the noisy variants: #116's shares over the
199 differing pairs, renormalized over the four kinds (#119)."""

BAND_TARGETS: tuple[tuple[str, float, float, float], ...] = (
    ("[0.9, 1.0)", 0.9, 1.0, 0.362),
    ("[0.8, 0.9)", 0.8, 0.9, 0.095),
    ("[0.7, 0.8)", 0.7, 0.8, 0.126),
    ("[0.5, 0.7)", 0.5, 0.7, 0.095),
    ("below 0.5", 0.0, 0.5, 0.322),
)
"""Where a variant's normalized Levenshtein to its entry should fall: each
band's name, its bounds, and #116's measured share of differing readings
over the blind pairing (72, 19, 25, 19 and 64 of 199)."""

TOLERANCE = 0.07
"""How far a kind share or a band share may sit from its target, in share
points, on the made-up pool the test runs. #119 set 5; it was widened to 7
on #128 because the two middle bands cannot be reached with these kinds:
letters and punctuation are one edit or two by #116's own definition, so
43.7 percent of the mass can only land in the top two bands, and the
[0.7, 0.8) band #116 measured at 12.6 percent was carried by readings that
drop or replace whole words, a kind #119 chose not to imitate. On the real
catalog [0.8, 0.9) sits about 5 points over its target and [0.7, 0.8) about
5 under, and no strength constant moves either; every kind share and every
other band sits within 5. The two extra points cover the swing between
seeds, about 1.5 points on 3,840 variants."""

LETTERS_TWO = 0.1
"""How often a letters variant substitutes two letters rather than one."""

DIGITS_ALL = 0.5
"""How often a digits variant drops every digit-bearing word rather than one."""

PUNCTUATION_TWO = 0.1
"""How often a punctuation variant makes two edits rather than one."""

PUNCTUATION_MARKS = "-.:/,"
"""The marks a punctuation edit adds or swaps in, the five commonest in the
catalog's descriptions."""

NUMERIC_PIECES = (4, 7)
"""How many quantity, price or unit tokens an extra-words variant appends
when the bleed is a numeric cell, inclusive bounds."""

UNITS = ("pcs", "ea", "box", "kg", "m", "mm", "ml", "l", "pk", "ct", "set", "roll")
"""Unit-shaped tokens, standing for a unit cell."""

FRAGMENT_WORDS = (5, 14)
"""How many consecutive words of a singleton an extra-words variant appends
when the bleed is page text, inclusive bounds, capped at the singleton's."""

ATTEMPTS = 50
"""How many strength draws a kind gets, and how many kinds a variant gets,
before the run gives up on its entry."""


@dataclass(frozen=True)
class Query:
    """One normalized description sent to an arm, and what the right answer
    is: the SKU of the entry it was generated from."""

    text: str
    sku: str
    kind: QueryKind


SliceName = Literal["scored", "development"]


@dataclass(frozen=True)
class Slice:
    """The queries of one side of the split."""

    name: SliceName
    queries: tuple[Query, ...]

    @property
    def entries(self) -> int:
        return len({each.sku for each in self.queries})

    @property
    def exact(self) -> int:
        return sum(each.kind == "exact" for each in self.queries)

    @property
    def noisy(self) -> int:
        return len(self.queries) - self.exact


@dataclass(frozen=True)
class Share:
    """One noise kind's, or one similarity band's, share of the noisy
    variants, against its measured target."""

    name: str
    count: int
    share: float | None
    target: float


@dataclass(frozen=True)
class QuerySet:
    """Every answerable query, split, and what the noise model produced."""

    scored: Slice
    development: Slice
    kinds: tuple[Share, ...]
    bands: tuple[Share, ...]

    @property
    def slices(self) -> tuple[Slice, Slice]:
        return (self.scored, self.development)


def build_query_set(catalog: Catalog, seed: int = SEED) -> QuerySet:
    """The whole set from a catalog: the split, then every entry's exact query
    and two variants, drawn on one stream from the seed."""
    rng = random.Random(seed)
    order = list(catalog.entries)
    rng.shuffle(order)
    development = frozenset(
        each.sku for each in order[: len(order) // DEVELOPMENT_ONE_IN]
    )
    noise = _Noise(rng, catalog)
    scored: list[Query] = []
    held_out: list[Query] = []
    variants: list[tuple[Entry, Query]] = []
    for entry in catalog.entries:
        own = [Query(entry.description, entry.sku, "exact")]
        for _ in range(VARIANTS):
            variant = noise.variant(entry)
            variants.append((entry, variant))
            own.append(variant)
        (held_out if entry.sku in development else scored).extend(own)
    return QuerySet(
        Slice("scored", tuple(scored)),
        Slice("development", tuple(held_out)),
        _kind_shares(variants),
        _band_shares(variants),
    )


def _kind_shares(variants: Sequence[tuple[Entry, Query]]) -> tuple[Share, ...]:
    return tuple(
        Share(kind, count, share_of(count, len(variants)), KIND_SHARES[kind])
        for kind in NOISE_KINDS
        for count in [sum(each.kind == kind for _, each in variants)]
    )


def _band_shares(variants: Sequence[tuple[Entry, Query]]) -> tuple[Share, ...]:
    scores = [similarity(entry.description, each.text) for entry, each in variants]
    return tuple(
        Share(name, count, share_of(count, len(scores)), target)
        for name, low, high, target in BAND_TARGETS
        for count in [sum(low <= score < high for score in scores)]
    )


class _Noise:
    """The noise model over one catalog, drawing on one stream."""

    def __init__(self, rng: random.Random, catalog: Catalog) -> None:
        self.rng = rng
        self.descriptions = frozenset(each.description for each in catalog.entries)
        self.singletons = catalog.singletons
        self.takes_digits = frozenset(
            each.description
            for each in catalog.entries
            if any(
                drop not in self.descriptions for drop in _digit_drops(each.description)
            )
        )
        """The entries that can drop digits: at least one digit-bearing word
        beside a plain one, and at least one of the drops leaving a text
        that is no entry's description."""
        takes = len(self.takes_digits)
        self.digit_rate = (
            min(1.0, KIND_SHARES["digits dropped"] * len(catalog.entries) / takes)
            if takes
            else 0.0
        )
        """How often a variant whose entry can drop digits does: the rate that
        lands the kind's share over every variant on its target."""

    def variant(self, entry: Entry) -> Query:
        """One noisy variant of the entry: a kind drawn and kept, its text
        normalized, different from every entry's description. A text that is
        an entry's redraws the strength; a kind that never gets clear of the
        entries in `ATTEMPTS` draws is given up and another drawn."""
        for _ in range(ATTEMPTS):
            kind = self._kind(entry.description)
            for _ in range(ATTEMPTS):
                text = normalize_text(self._apply(kind, entry.description))
                if text and text not in self.descriptions:
                    return Query(text, entry.sku, kind)
        raise ResolutionError(
            f"no variant of {entry.sku} differs from every entry after {ATTEMPTS} kinds"
        )

    def _kind(self, text: str) -> NoiseKind:
        if text in self.takes_digits and self.rng.random() < self.digit_rate:
            return "digits dropped"
        kinds = [
            kind
            for kind in NOISE_KINDS
            if kind != "digits dropped"
            and (kind != "letters substituted" or _takes_letters(text))
        ]
        return self.rng.choices(kinds, [KIND_SHARES[kind] for kind in kinds])[0]

    def _apply(self, kind: NoiseKind, text: str) -> str:
        if kind == "extra words":
            return self._extra_words(text)
        if kind == "letters substituted":
            return self._letters(text)
        if kind == "digits dropped":
            return self._digits(text)
        return self._punctuation(text)

    def _letters(self, text: str) -> str:
        positions = [index for index, char in enumerate(text) if char.isalpha()]
        count = 2 if len(positions) > 1 and self.rng.random() < LETTERS_TWO else 1
        letters = list(text)
        for index in self.rng.sample(positions, count):
            others = [char for char in string.ascii_lowercase if char != letters[index]]
            letters[index] = self.rng.choice(others)
        return "".join(letters)

    def _digits(self, text: str) -> str:
        """The longest digit-bearing word dropped, the part number rather
        than a count, or every one of them; never a drop that is an entry."""
        *ones, every = _digit_drops(text)
        ones = [each for each in ones if each not in self.descriptions]
        if not ones or (
            every not in self.descriptions and self.rng.random() < DIGITS_ALL
        ):
            return every
        return self.rng.choice(ones)

    def _punctuation(self, text: str) -> str:
        edits = 2 if self.rng.random() < PUNCTUATION_TWO else 1
        for _ in range(edits):
            marks = [
                index for index, char in enumerate(text) if char in string.punctuation
            ]
            operation = self.rng.choice(["drop", "swap", "add"] if marks else ["add"])
            if operation == "add":
                words = text.split()
                at = self.rng.randrange(len(words))
                words[at] += self.rng.choice(PUNCTUATION_MARKS)
                text = " ".join(words)
                continue
            index = self.rng.choice(marks)
            if operation == "swap":
                others = [mark for mark in PUNCTUATION_MARKS if mark != text[index]]
                mark = self.rng.choice(others)
            else:
                # Dropped outright, a joined word staying joined: #116 counted
                # a reading as punctuation when both sides agree once the
                # marks are removed, and a space in the mark's place would be
                # the spacing kind #119 left out.
                mark = ""
            text = text[:index] + mark + text[index + 1 :]
        return text

    def _extra_words(self, text: str) -> str:
        for _ in range(ATTEMPTS):
            if not self.singletons or self.rng.random() < 0.5:
                added, in_front = self._numeric(), False
            else:
                added, in_front = self._fragment(), self.rng.random() < 0.5
            if not self._carries_entry(added):
                break
        return f"{added} {text}" if in_front else f"{text} {added}"

    def _numeric(self) -> str:
        pieces = []
        for _ in range(self.rng.randint(*NUMERIC_PIECES)):
            shape = self.rng.choice(["quantity", "price", "unit"])
            if shape == "quantity":
                pieces.append(str(self.rng.randint(1, 999)))
            elif shape == "price":
                pieces.append(f"{self.rng.uniform(0.5, 5000):,.2f}")
            else:
                pieces.append(self.rng.choice(UNITS))
        return " ".join(pieces)

    def _fragment(self) -> str:
        words = self.rng.choice(self.singletons).split()
        span = min(len(words), self.rng.randint(*FRAGMENT_WORDS))
        start = self.rng.randrange(len(words) - span + 1)
        return " ".join(words[start : start + span])

    def _carries_entry(self, added: str) -> bool:
        """Whether any run of the added words is an entry's whole description."""
        words = added.split()
        return any(
            " ".join(words[start:end]) in self.descriptions
            for start in range(len(words))
            for end in range(start + 1, len(words) + 1)
        )


def _has_digit(word: str) -> bool:
    return any(char.isdigit() for char in word)


def _digit_drops(text: str) -> tuple[str, ...]:
    """Every text dropping digits can leave: one per longest digit-bearing
    word dropped alone, then every digit-bearing word dropped, last. Empty
    when the text has no digit-bearing word or no plain word beside them."""
    words = text.split()
    bearing = [index for index, word in enumerate(words) if _has_digit(word)]
    if not 0 < len(bearing) < len(words):
        return ()
    longest = max(len(words[index]) for index in bearing)
    drops = [
        _without(words, {index}) for index in bearing if len(words[index]) == longest
    ]
    return (*dict.fromkeys(drops), _without(words, set(bearing)))


def _without(words: Sequence[str], dropped: set[int]) -> str:
    return " ".join(word for index, word in enumerate(words) if index not in dropped)


def _takes_letters(text: str) -> bool:
    return any(char.isalpha() for char in text)
