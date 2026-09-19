"""Tests for the similarity pairing compares codes and descriptions by."""

import pytest

from docmatch.matching.similarity import similarity


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
