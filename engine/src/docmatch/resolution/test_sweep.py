"""Tests for the depth rule: the smallest value whose development top-5 is
within one point of the grid's best, ties to the cheaper; and for the
keep-or-drop verdict ADR 0001 fixed. Pure, every score made up so each
branch of each rule fires."""

from docmatch.resolution.arms import TOP
from docmatch.resolution.sweep import (
    CEILING_MS,
    DEPTH,
    DEPTHS,
    MARGIN,
    POINT,
    RERANK_DEPTH,
    RERANK_DEPTHS,
    Point,
    Sweep,
    Verdict,
    choose,
)


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


def test_the_n_grid_is_the_one_the_spec_fixed_and_never_below_ten() -> None:
    """At least 10, twice the five, so the five always come from reranked
    candidates (#120)."""
    assert RERANK_DEPTHS == (10, 25, 50)
    assert min(RERANK_DEPTHS) >= 2 * TOP
    assert RERANK_DEPTH in RERANK_DEPTHS


def test_the_verdict_constants_are_the_ones_adr_0001_fixed() -> None:
    assert MARGIN == 0.01
    assert CEILING_MS == 500.0


def test_the_reranker_is_kept_when_it_clears_the_margin_under_the_ceiling() -> None:
    verdict = Verdict(rerank_top1=0.950, hybrid_top1=0.917, p95_ms=340.0)

    assert verdict.gain is not None and round(verdict.gain, 3) == 0.033
    assert verdict.clears_margin
    assert verdict.under_ceiling
    assert verdict.kept


def test_a_gain_exactly_one_point_clears_the_margin() -> None:
    """0.927 less 0.917 is a hair under 0.01 in binary floating point; the
    rule counts it as one point, as a reader of the table would."""
    assert Verdict(0.927, 0.917, 100.0).clears_margin
    assert Verdict(0.927, 0.917, 100.0).kept


def test_a_gain_under_one_point_drops_the_reranker() -> None:
    verdict = Verdict(0.926, 0.917, 100.0)

    assert not verdict.clears_margin
    assert verdict.under_ceiling
    assert verdict.kept is False


def test_a_p95_exactly_at_the_ceiling_is_under_it() -> None:
    """At most 500 ms, so exactly 500 passes."""
    assert Verdict(0.950, 0.917, 500.0).under_ceiling
    assert Verdict(0.950, 0.917, 500.0).kept


def test_a_p95_over_the_ceiling_drops_the_reranker_whatever_it_gains() -> None:
    verdict = Verdict(0.990, 0.917, 500.01)

    assert verdict.clears_margin
    assert not verdict.under_ceiling
    assert verdict.kept is False


def test_no_headline_on_either_arm_gives_no_verdict() -> None:
    assert Verdict(None, 0.917, 100.0).gain is None
    assert Verdict(None, 0.917, 100.0).kept is None
    assert Verdict(0.917, None, 100.0).kept is None
