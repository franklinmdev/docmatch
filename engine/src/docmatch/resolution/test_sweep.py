"""Tests for the depth rule: the smallest value whose development top-5 is
within one point of the grid's best, ties to the cheaper. Pure, every score
made up so each branch of the rule fires."""

from docmatch.resolution.sweep import DEPTH, DEPTHS, POINT, Point, Sweep, choose


def points(*scores: float | None) -> tuple[Point, ...]:
    return tuple(
        Point(value, score, 1152) for value, score in zip(DEPTHS, scores, strict=True)
    )


def test_the_grid_and_the_rule_are_the_ones_the_spec_fixed() -> None:
    assert DEPTHS == (25, 50, 100)
    assert POINT == 0.01
    assert DEPTH in DEPTHS


def test_a_clear_winner_is_chosen_when_nothing_smaller_comes_within_a_point() -> None:
    assert choose(points(0.900, 0.950, 0.975)) == 100


def test_a_smaller_value_within_one_point_of_the_best_is_chosen() -> None:
    assert choose(points(0.955, 0.968, 0.975)) == 50


def test_a_value_exactly_one_point_below_the_best_is_within_it() -> None:
    """0.96 less 0.95 is a hair over 0.01 in binary floating point; the rule
    counts it as one point, which is what a reader of the table would."""
    assert choose(points(0.950, 0.960, 0.960)) == 25


def test_a_tie_goes_to_the_cheaper_value() -> None:
    assert choose(points(0.960, 0.980, 0.980)) == 50


def test_a_grid_with_no_score_chooses_nothing() -> None:
    assert choose(points(None, None, None)) is None
    assert choose(()) is None


def test_a_sweep_says_the_constant_and_what_the_procedure_chose() -> None:
    sweep = Sweep("d", 50, points(0.990, 0.992, 0.993))

    assert sweep.chosen == 25
    assert sweep.constant == 50
