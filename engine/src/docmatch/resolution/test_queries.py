"""Tests for the query set on hand-built catalogs: seam 1 of the Phase 3
spec, pure. Every description is made up.

The calibration runs over a made-up pool shaped like the real catalog's
measured lengths, since train never enters CI: entry lengths at p10, p50 and
p90 of 8, 17 and 36 characters, about two entries in five carrying a
digit-bearing word beside a plain one, about one in two carrying punctuation,
and a singleton pool at 11, 22 and 62 characters (#117, #119).
"""

import random
import re
import statistics
import string
from collections.abc import Sequence

import pytest

from docmatch.metrics.normalization import normalize_text
from docmatch.resolution import queries
from docmatch.resolution.catalog import (
    Catalog,
    CatalogCounts,
    Entry,
    ResolutionError,
    build_catalog,
    mint,
)
from docmatch.resolution.queries import (
    NOISE_KINDS,
    SEED,
    TOLERANCE,
    UNITS,
    Query,
    QuerySet,
    build_query_set,
)
from docmatch.resolution.test_catalog import described


def catalog_of(*descriptions: str, singletons: Sequence[str] = ()) -> Catalog:
    """A catalog straight from canonical descriptions, no documents needed."""
    entries = sorted(
        (Entry(mint(each), each) for each in descriptions), key=lambda each: each.sku
    )
    counts = CatalogCounts(2, 2 * len(entries), len(entries), len(entries))
    return Catalog(tuple(entries), counts, tuple(sorted(singletons)))


SYLLABLES = ("ba", "ko", "ri", "tu", "mel", "san", "dor", "vi", "lux", "pen", "tor")
"""No `q` anywhere, so a `qq` word can mark singleton text."""

ENTRY_LENGTHS = ((0.0, 3), (0.1, 8), (0.5, 17), (0.9, 36), (1.0, 90))
SINGLETON_LENGTHS = ((0.0, 4), (0.1, 11), (0.5, 22), (0.9, 62), (1.0, 120))
"""Quantile functions, (share, characters), interpolated between the points."""

DIGIT_SHARE = 0.37
PUNCTUATION_SHARE = 0.45
MARK = "qq"


def shaped_pool(
    entries: int = 1000,
    marked: bool = False,
    seed: int = 1,
    singletons: int | None = None,
) -> Catalog:
    """A made-up catalog shaped like the real one's measured lengths, with
    a singleton pool, half the entries unless `singletons` says; `marked`
    starts every singleton word with `qq`."""
    rng = random.Random(seed)
    descriptions: set[str] = set()
    while len(descriptions) < entries:
        descriptions.add(
            _description(
                rng,
                _length(rng, ENTRY_LENGTHS),
                digits=rng.random() < DIGIT_SHARE,
                punctuation=rng.random() < PUNCTUATION_SHARE,
            )
        )
    pool: set[str] = set()
    while len(pool) < (entries // 2 if singletons is None else singletons):
        pool.add(
            _description(
                rng,
                _length(rng, SINGLETON_LENGTHS),
                digits=not marked and rng.random() < DIGIT_SHARE,
                punctuation=rng.random() < PUNCTUATION_SHARE,
                mark=MARK if marked else "",
            )
        )
    return catalog_of(*descriptions, singletons=tuple(pool - descriptions))


def _length(rng: random.Random, quantiles: Sequence[tuple[float, int]]) -> int:
    drawn = rng.random()
    for (low, start), (high, end) in zip(quantiles, quantiles[1:], strict=False):
        if drawn <= high:
            return round(start + (end - start) * (drawn - low) / (high - low))
    return quantiles[-1][1]


def _description(
    rng: random.Random, length: int, digits: bool, punctuation: bool, mark: str = ""
) -> str:
    token = ""
    if digits:
        token = (
            str(rng.randint(1, 999))
            if rng.random() < 0.5
            else "".join(
                rng.choices(
                    string.ascii_lowercase + string.digits * 3, k=rng.randint(4, 9)
                )
            )
        )
        if not any(char.isdigit() for char in token):
            token += "7"
        length = max(length - len(token) - 1, 2)
    words: list[str] = []
    while len(" ".join(words)) < length:
        words.append(mark + _word(rng, rng.randint(2, 9)))
    excess = len(" ".join(words)) - length
    if 0 < excess < len(words[-1]) - len(mark) - 1:
        words[-1] = words[-1][:-excess]
    if token:
        words.insert(rng.randrange(len(words) + 1), token)
    if punctuation:
        shape = rng.choice(["comma", "hyphen", "period"])
        if shape == "comma" and len(words) > 1:
            words[0] += ","
        elif shape == "hyphen" and len(words) > 1:
            words[0:2] = [words[0] + "-" + words[1]]
        else:
            words[-1] += "."
    return normalize_text(" ".join(words))


def _word(rng: random.Random, length: int) -> str:
    word = ""
    while len(word) < length:
        word += rng.choice(SYLLABLES)
    return word[:length]


def variants_of(query_set: QuerySet, catalog: Catalog) -> list[tuple[Entry, Query]]:
    """Every noisy variant beside its entry."""
    by_sku: dict[str | None, Entry] = {each.sku: each for each in catalog.entries}
    return [
        (by_sku[each.sku], each)
        for one in query_set.slices
        for each in one.queries
        if each.kind != "exact"
    ]


def added_text(entry: Entry, variant: Query) -> str:
    """What an extra-words variant added at either end of its entry."""
    if variant.text.startswith(entry.description):
        return variant.text[len(entry.description) :]
    assert variant.text.endswith(entry.description)
    return variant.text[: -len(entry.description)]


def without_punctuation(text: str) -> str:
    return " ".join("".join(c for c in text if c not in string.punctuation).split())


def test_every_entry_carries_one_exact_query_and_two_noisy_variants() -> None:
    catalog = catalog_of(
        "blue widget 200 mm", "red gadget, box of 12", "green thing", "plain"
    )

    query_set = build_query_set(catalog)

    queries = query_set.scored.queries + query_set.development.queries
    for entry in catalog.entries:
        own = [each for each in queries if each.sku == entry.sku]
        exact = [each for each in own if each.kind == "exact"]
        noisy = [each for each in own if each.kind != "exact"]
        assert [each.text for each in exact] == [entry.description]
        assert len(noisy) == 2
        for variant in noisy:
            assert variant.kind in NOISE_KINDS
            assert variant.text == normalize_text(variant.text)
            assert variant.text != entry.description
            assert variant.text
    assert len(queries) == 3 * len(catalog.entries)


def test_a_variant_keeps_one_kind_and_its_text_shows_which() -> None:
    """Each kind leaves the mark #116 classified it by: extra words carry
    the entry whole, letters keep the length and change at most two letters,
    digits leave the plain words in order and only digit-bearing ones gone,
    punctuation agrees with the entry once every mark is dropped."""
    catalog = shaped_pool()

    for entry, variant in variants_of(build_query_set(catalog), catalog):
        if variant.kind == "extra words":
            assert entry.description in variant.text
            assert len(variant.text) > len(entry.description)
        elif variant.kind == "letters substituted":
            assert len(variant.text) == len(entry.description)
            changed = [
                index
                for index, (was, now) in enumerate(
                    zip(entry.description, variant.text, strict=True)
                )
                if was != now
            ]
            assert 1 <= len(changed) <= 2
            assert all(entry.description[index].isalpha() for index in changed)
            assert all(variant.text[index].isalpha() for index in changed)
        elif variant.kind == "digits dropped":
            kept = iter(variant.text.split())
            pending = next(kept, None)
            dropped = []
            for word in entry.description.split():
                if word == pending:
                    pending = next(kept, None)
                else:
                    dropped.append(word)
            assert pending is None
            assert dropped
            assert all(any(c.isdigit() for c in word) for word in dropped)
        else:
            assert variant.kind == "punctuation"
            assert without_punctuation(variant.text) == without_punctuation(
                entry.description
            )
            assert variant.text != entry.description


def test_kind_shares_and_bands_land_within_tolerance_on_the_shaped_pool() -> None:
    query_set = build_query_set(shaped_pool())

    for kind in query_set.kinds:
        assert kind.share is not None
        assert abs(kind.share - kind.target) <= TOLERANCE, kind
    for band in query_set.bands:
        assert band.share is not None
        assert abs(band.share - band.target) <= TOLERANCE, band
    assert [each.name for each in query_set.kinds] == list(NOISE_KINDS)
    assert sum(each.count for each in query_set.bands) == sum(
        each.count for each in query_set.kinds
    )


NUMERIC_OR_UNIT = re.compile(r"^(\d[\d,]*(\.\d+)?|" + "|".join(UNITS) + r")$")


def test_the_extra_words_bleed_never_draws_from_a_catalog_entry() -> None:
    """Half the added text is numeric or unit-shaped, half a fragment of a
    singleton, marked here so it can be told apart, and none of it is an
    entry's description; the median growth sits in #116's measured band."""
    catalog = shaped_pool(marked=True)
    descriptions = {each.description for each in catalog.entries}
    growth = []
    fragments = numeric = 0

    for entry, variant in variants_of(build_query_set(catalog), catalog):
        if variant.kind != "extra words":
            continue
        added = added_text(entry, variant).split()
        assert added
        assert " ".join(added) not in descriptions
        if all(word.startswith(MARK) for word in added):
            fragments += 1
        else:
            assert all(NUMERIC_OR_UNIT.match(word) for word in added), added
            numeric += 1
        growth.append(len(variant.text) - len(entry.description))

    assert 14 <= statistics.median(growth) <= 32
    assert abs(fragments - numeric) <= 0.1 * (fragments + numeric)


def test_a_bleed_that_carries_an_entry_is_a_rejected_draw_never_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Added text carrying an entry's description is redrawn like a text that
    is an entry, and a kind that never gets clear is given up: with one draw
    per kind and every singleton an entry, the fragment bleed can never get
    clear, so the run says so rather than keeping the entry it carries."""
    entries = [f"{word} widget" for word in ("alpha", "bravo", "delta", "echo")]
    entries += [f"{word} gadget" for word in ("kilo", "lima", "mike", "oscar")]
    catalog = catalog_of(*entries, singletons=entries)
    monkeypatch.setattr(queries, "ATTEMPTS", 1)

    with pytest.raises(ResolutionError, match="differs from every entry"):
        build_query_set(catalog)


def test_a_catalog_without_singletons_bleeds_numeric_cells_only() -> None:
    catalog = catalog_of("blue widget", "red gadget", "green thing", "plain", "other")

    for entry, variant in variants_of(build_query_set(catalog), catalog):
        if variant.kind == "extra words":
            added = added_text(entry, variant).split()
            assert all(NUMERIC_OR_UNIT.match(word) for word in added), added


def test_the_split_sends_one_entry_in_five_to_development_under_the_seed() -> None:
    catalog = shaped_pool()

    one = build_query_set(catalog)
    again = build_query_set(catalog)
    other = build_query_set(catalog, seed=SEED + 1)

    assert one == again
    assert (one.scored.entries, one.development.entries) == (800, 200)
    assert (one.scored.exact, one.scored.noisy, len(one.scored.queries)) == (
        800,
        1600,
        2400,
    )
    assert (one.development.exact, one.development.noisy) == (200, 400)
    scored = {each.sku for each in one.scored.queries}
    development = {each.sku for each in one.development.queries}
    assert scored.isdisjoint(development)
    assert scored | development == {each.sku for each in catalog.entries}
    assert {each.sku for each in other.development.queries} != development
    assert [each.text for each in other.scored.queries] != [
        each.text for each in one.scored.queries
    ]


def test_a_catalog_of_six_leaves_one_development_entry_and_four_none() -> None:
    six = build_query_set(catalog_of(*(f"item {n} thing" for n in range(6))))
    four = build_query_set(catalog_of(*(f"item {n} thing" for n in range(4))))

    assert (six.scored.entries, six.development.entries) == (5, 1)
    assert (four.scored.entries, four.development.entries) == (4, 0)
    assert four.development.queries == ()


def test_a_pool_with_no_repeats_yields_an_empty_set_with_no_shares() -> None:
    catalog = build_catalog([described("Blue widget"), described("Red widget")])

    query_set = build_query_set(catalog)

    assert query_set.scored.queries == () and query_set.development.queries == ()
    assert all(each.share is None and each.count == 0 for each in query_set.kinds)
    assert all(each.share is None and each.count == 0 for each in query_set.bands)


def test_the_exact_query_is_the_canonical_description_with_its_sku() -> None:
    catalog = build_catalog(
        [
            described("Blue widget", "Red widget"),
            described("Blue widget", "Red widget"),
        ]
    )

    exact = [
        each
        for one in build_query_set(catalog).slices
        for each in one.queries
        if each.kind == "exact"
    ]

    assert sorted(exact, key=lambda each: str(each.sku)) == [
        Query(each.description, each.sku, "exact") for each in catalog.entries
    ]


def test_the_targets_are_the_measured_shares() -> None:
    query_set = build_query_set(shaped_pool(entries=20))

    assert [each.target for each in query_set.kinds] == [0.350, 0.245, 0.212, 0.192]
    assert [each.target for each in query_set.bands] == pytest.approx(
        [0.362, 0.095, 0.126, 0.095, 0.322]
    )
    assert [each.name for each in query_set.bands] == [
        "[0.9, 1.0)",
        "[0.8, 0.9)",
        "[0.7, 0.8)",
        "[0.5, 0.7)",
        "below 0.5",
    ]


def out_of_catalog(query_set: QuerySet) -> list[Query]:
    return [each for one in query_set.slices for each in one.out_of_catalog]


def test_out_of_catalog_is_0_150_of_the_set_split_and_stratified_like_entries() -> None:
    """At the real catalog's size the draw gives #119's counts: 1,016 of
    6,776, 813 scored and 203 development, each slice one exact to two
    noisy. Every out-of-catalog query carries no SKU; an exact one is its
    singleton unchanged, a noisy one is no entry's description."""
    catalog = shaped_pool(entries=1920, marked=True, singletons=1200)
    descriptions = {each.description for each in catalog.entries}

    query_set = build_query_set(catalog)

    counted = [
        (
            sum(each.kind == "exact" for each in one.out_of_catalog),
            sum(each.kind != "exact" for each in one.out_of_catalog),
        )
        for one in query_set.slices
    ]
    assert counted == [(271, 542), (68, 135)]
    for query in out_of_catalog(query_set):
        assert query.sku is None
        assert query.text == normalize_text(query.text)
        assert query.text not in descriptions
        if query.kind == "exact":
            assert query.text in catalog.singletons
        else:
            assert query.kind in NOISE_KINDS
    exact = [each.text for each in out_of_catalog(query_set) if each.kind == "exact"]
    assert len(set(exact)) == len(exact), "one query per description"
    assert (len(query_set.scored.queries), len(query_set.development.queries)) == (
        4608,
        1152,
    )


GUARDED = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango",
)  # fmt: skip


def test_a_singleton_at_or_above_the_guard_to_any_entry_is_never_drawn() -> None:
    """Twenty entries ask for eleven out-of-catalog queries and the pool
    holds five singletons, one of them an entry respelled by one letter:
    the four others are all drawn and the respelled one never is, under any
    seed."""
    entries = [f"heavy duty {word} bracket, galvanised steel" for word in GUARDED]
    respelled = "heavy duty alpha brackot, galvanised steel"
    far = ["freight and handling", "service call", "labour 2 hours", "fuel surcharge"]
    catalog = catalog_of(*entries, singletons=[respelled, *far])

    for seed in range(SEED, SEED + 20):
        drawn = out_of_catalog(build_query_set(catalog, seed=seed))
        assert len(drawn) == 4
        assert all(each.text != respelled for each in drawn)
        assert {each.text for each in drawn if each.kind == "exact"} <= set(far)


def test_the_out_of_catalog_draw_repeats_under_the_seed_and_differs_under_another() -> (
    None
):
    catalog = shaped_pool(entries=200, marked=True, singletons=300)

    one = out_of_catalog(build_query_set(catalog))
    again = out_of_catalog(build_query_set(catalog))
    other = out_of_catalog(build_query_set(catalog, seed=SEED + 1))

    assert one == again
    assert len(one) == round(0.15 * 600 / 0.85)
    assert [each.text for each in other] != [each.text for each in one]


def test_a_catalog_of_six_with_four_singletons_draws_three_all_scored() -> None:
    """The fixture's shape: eighteen answerable queries ask for three out of
    catalog, one exact and two noisy, too few for development to get any."""
    catalog = catalog_of(
        *(f"item {n} thing" for n in range(6)),
        singletons=["freight charge", "fuel surcharge", "labour", "service call"],
    )

    query_set = build_query_set(catalog)

    assert query_set.development.out_of_catalog == ()
    assert sorted(each.kind == "exact" for each in query_set.scored.out_of_catalog) == [
        False,
        False,
        True,
    ]


def test_an_empty_catalog_draws_no_out_of_catalog_query() -> None:
    catalog = build_catalog([described("Blue widget"), described("Red widget")])

    assert out_of_catalog(build_query_set(catalog)) == []
