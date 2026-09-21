"""Tests for the similarity pairing compares codes and descriptions by."""

import random

import pytest

from docmatch.matching.similarity import at_least, similarity


@pytest.mark.parametrize(
    ("one", "other", "expected"),
    [
        ("blue widget", "blue widget", 1.0),
        ("kitten", "sitting", 1 - 3 / 7),
        ("abc", "xyz", 0.0),
        ("abc", "", 0.0),
        ("", "", 1.0),
        ("a4 paper", "a4 paper, 500 sheets", 1 - 12 / 20),
    ],
)
def test_similarity_is_one_minus_edits_over_the_longer_length(
    one: str, other: str, expected: float
) -> None:
    assert similarity(one, other) == pytest.approx(expected)


def test_similarity_is_symmetric() -> None:
    assert similarity("torque wrench", "hex key set") == similarity(
        "hex key set", "torque wrench"
    )


def test_at_least_agrees_with_similarity_on_every_pair_and_floor() -> None:
    """The bounded check gives up early on a pair that cannot reach the
    floor, and must still decide exactly as the whole table would."""
    rng = random.Random(7)
    texts = ["".join(rng.choices("abc -1", k=rng.randint(0, 12))) for _ in range(80)]
    texts += ["kitten", "sitting", "blue widget", "blue widgets", "blue wodget"]

    for one in texts:
        for other in texts:
            for floor in (0.0, 0.5, 0.9, 0.95, 1.0):
                assert at_least(one, other, floor) == (
                    similarity(one, other) >= floor
                ), (one, other, floor)
