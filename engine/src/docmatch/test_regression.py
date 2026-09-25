"""Tests for the regression gate: which paths demand what, and what fails.

Every comparison here runs on hand-built aggregates and a hand-built tree
listing, so no git, no DocILE and no model is involved; the git helpers are
tested apart, on a repository made for the test.
"""

import hashlib
import json
from pathlib import Path

import pytest

from docmatch.conftest import git
from docmatch.regression import (
    EXTRACTION_NOISE,
    Aggregate,
    Noise,
    RegressionError,
    Row,
    Stamp,
    changed_paths,
    check,
    demanded,
    dirty_paths,
    extraction_path,
    extraction_tree,
    has_reason,
    listing,
    measured_row,
    read_aggregate,
    read_event,
    scoring_path,
    scoring_tree,
    show,
    write_row,
)

ROOT = "engine/src/docmatch"

LISTING = (
    (f"{ROOT}/extraction/extractor.py", "a1"),
    (f"{ROOT}/extraction/gemini.py", "g1"),
    (f"{ROOT}/extraction/azure.py", "z1"),
    (f"{ROOT}/extraction/openai.py", "o1"),
    (f"{ROOT}/extraction/derived.py", "d1"),
    (f"{ROOT}/extraction/test_gemini.py", "t1"),
    (f"{ROOT}/metrics/fields.py", "f1"),
    (f"{ROOT}/evals/run.py", "e1"),
    (f"{ROOT}/evals/subset.json", "s1"),
    (f"{ROOT}/gate.py", "q1"),
    (f"{ROOT}/docile/dataset.py", "c1"),
    (f"{ROOT}/matching/matcher.py", "m1"),
)


def with_blob(path: str, blob: str) -> tuple[tuple[str, str], ...]:
    """The listing with one file changed, or added when it was not there."""
    rest = tuple(each for each in LISTING if each[0] != path)
    return (*rest, (path, blob))


# The two path lists.


@pytest.mark.parametrize(
    ("path", "backends"),
    [
        (f"{ROOT}/extraction/extractor.py", {"gemini", "azure", "openai"}),
        (f"{ROOT}/extraction/run.py", {"gemini", "azure", "openai"}),
        (f"{ROOT}/extraction/schema.py", {"gemini", "azure", "openai"}),
        (f"{ROOT}/extraction/gemini.py", {"gemini"}),
        (f"{ROOT}/extraction/azure.py", {"azure"}),
        (f"{ROOT}/extraction/openai.py", {"openai"}),
        (f"{ROOT}/extraction/derived.py", set()),
        (f"{ROOT}/extraction/test_gemini.py", set()),
        (f"{ROOT}/extraction/conftest.py", set()),
        (f"{ROOT}/metrics/fields.py", set()),
    ],
)
def test_an_extraction_path_is_shared_or_one_backends_own(
    path: str, backends: set[str]
) -> None:
    assert {b for b in EXTRACTION_NOISE if extraction_path(path, b)} == backends


@pytest.mark.parametrize(
    ("path", "scoring"),
    [
        (f"{ROOT}/metrics/fields.py", True),
        (f"{ROOT}/evals/run.py", True),
        (f"{ROOT}/evals/subset.json", True),
        (f"{ROOT}/extraction/derived.py", True),
        (f"{ROOT}/gate.py", True),
        (f"{ROOT}/docile/dataset.py", True),
        (f"{ROOT}/metrics/test_fields.py", False),
        (f"{ROOT}/docile/fixtures/annotations/a.json", False),
        (f"{ROOT}/test_gate.py", False),
        (f"{ROOT}/extraction/gemini.py", False),
        (f"{ROOT}/matching/matcher.py", False),
        (f"{ROOT}/regression.py", False),
        (f"{ROOT}/cli.py", False),
        ("README.md", False),
    ],
)
def test_the_scoring_paths(path: str, scoring: bool) -> None:
    assert scoring_path(path) is scoring


def test_a_shared_extraction_file_demands_every_row_re_extracted() -> None:
    demand = demanded([f"{ROOT}/extraction/pages.py"])
    assert demand.re_extract == {"gemini", "azure", "openai"}
    assert not demand.re_score


def test_a_backends_module_demands_only_its_row() -> None:
    assert demanded([f"{ROOT}/extraction/azure.py"]).re_extract == {"azure"}


def test_a_scoring_file_demands_every_row_re_scored() -> None:
    demand = demanded([f"{ROOT}/extraction/derived.py"])
    assert demand.re_extract == set()
    assert demand.re_score


def test_anything_else_demands_nothing() -> None:
    demand = demanded([f"{ROOT}/matching/matcher.py", "README.md"])
    assert demand.re_extract == set()
    assert not demand.re_score


# Trees.


def test_a_tree_moves_with_the_files_in_its_list_only() -> None:
    before = extraction_tree(LISTING, "gemini")
    assert (
        extraction_tree(with_blob(f"{ROOT}/extraction/gemini.py", "g2"), "gemini")
        != before
    )
    assert (
        extraction_tree(with_blob(f"{ROOT}/extraction/azure.py", "z2"), "gemini")
        == before
    )
    assert (
        extraction_tree(with_blob(f"{ROOT}/extraction/test_gemini.py", "t2"), "gemini")
        == before
    )
    assert scoring_tree(with_blob(f"{ROOT}/evals/run.py", "e2")) != scoring_tree(
        LISTING
    )
    assert scoring_tree(with_blob(f"{ROOT}/matching/matcher.py", "m2")) == scoring_tree(
        LISTING
    )


def test_a_new_file_in_the_list_moves_the_tree() -> None:
    added = with_blob(f"{ROOT}/metrics/new.py", "n1")
    assert scoring_tree(added) != scoring_tree(LISTING)


def test_a_tree_does_not_depend_on_the_order_of_the_listing() -> None:
    assert scoring_tree(tuple(reversed(LISTING))) == scoring_tree(LISTING)


# The comparison.


def a_row(
    backend: str = "gemini",
    *,
    field_f1: float = 0.615,
    line_item_f1: float = 0.374,
    predictions: str = "p1",
    listing: tuple[tuple[str, str], ...] = LISTING,
) -> Row:
    return Row(
        requested_model="model",
        field_f1=field_f1,
        line_item_f1=line_item_f1,
        predictions=predictions,
        extracted_on=Stamp(commit="c1", tree=extraction_tree(listing, backend)),
        scored_on=Stamp(commit="c1", tree=scoring_tree(listing)),
    )


def three(**rows: Row) -> Aggregate:
    return {
        "gemini": a_row("gemini"),
        "azure": a_row("azure"),
        "openai": a_row("openai"),
        **rows,
    }


NOISE = {b: Noise(field_f1=0.02, line_item_f1=0.03) for b in EXTRACTION_NOISE}


def test_an_unchanged_aggregate_on_a_pr_touching_neither_list_passes() -> None:
    verdict = check(three(), three(), [f"{ROOT}/matching/matcher.py"], LISTING)
    assert verdict.passed
    assert not verdict.stale
    assert not any(each.regressed for each in verdict.comparisons)


def test_the_comparison_lists_both_metrics_for_every_backend() -> None:
    verdict = check(three(), three(), [], LISTING)
    assert [(each.backend, each.metric) for each in verdict.comparisons] == [
        ("gemini", "field_f1"),
        ("gemini", "line_item_f1"),
        ("azure", "field_f1"),
        ("azure", "line_item_f1"),
        ("openai", "field_f1"),
        ("openai", "line_item_f1"),
    ]


def test_a_re_score_dropping_by_any_amount_fails() -> None:
    changed = [f"{ROOT}/metrics/fields.py"]
    after = with_blob(changed[0], "f2")
    head = {b: a_row(b, listing=after) for b in EXTRACTION_NOISE}
    head["azure"] = a_row("azure", line_item_f1=0.374 - 1e-9, listing=after)

    verdict = check(three(), head, changed, after, noise=NOISE)

    assert not verdict.passed
    assert [(each.backend, each.metric, each.kind) for each in verdict.regressions] == [
        ("azure", "line_item_f1", "re-scored")
    ]


def test_a_re_scored_rise_passes() -> None:
    changed = [f"{ROOT}/metrics/fields.py"]
    after = with_blob(changed[0], "f2")
    head = {b: a_row(b, field_f1=0.7, listing=after) for b in EXTRACTION_NOISE}
    assert check(three(), head, changed, after, noise=NOISE).passed


def test_a_re_extraction_within_its_noise_passes() -> None:
    changed = [f"{ROOT}/extraction/gemini.py"]
    after = with_blob(changed[0], "g2")
    head = three(gemini=a_row(field_f1=0.615 - 0.02, predictions="p2", listing=after))

    verdict = check(three(), head, changed, after, noise=NOISE)

    assert verdict.passed
    gemini = verdict.comparisons[0]
    assert (gemini.kind, gemini.allowed) == ("re-extracted", 0.02)


def test_a_re_extraction_beyond_its_noise_fails() -> None:
    changed = [f"{ROOT}/extraction/gemini.py"]
    after = with_blob(changed[0], "g2")
    head = three(
        gemini=a_row(line_item_f1=0.374 - 0.031, predictions="p2", listing=after)
    )

    verdict = check(three(), head, changed, after, noise=NOISE)

    assert [(each.metric, each.kind) for each in verdict.regressions] == [
        ("line_item_f1", "re-extracted")
    ]


def test_noise_covers_only_a_re_extraction_the_change_owed() -> None:
    """A re-extraction a scoring change did not owe could hide a scoring drop
    inside the noise, so it is held to any drop."""
    changed = [f"{ROOT}/metrics/fields.py"]
    after = with_blob(changed[0], "f2")
    head = {b: a_row(b, listing=after) for b in EXTRACTION_NOISE}
    head["gemini"] = a_row(field_f1=0.615 - 0.01, predictions="p2", listing=after)

    verdict = check(three(), head, changed, after, noise=NOISE)

    assert [(each.metric, each.kind, each.allowed) for each in verdict.regressions] == [
        ("field_f1", "re-extracted", 0.0)
    ]


def test_extraction_noise_is_strict_until_it_is_measured() -> None:
    assert all(
        each == Noise(field_f1=0.0, line_item_f1=0.0)
        for each in EXTRACTION_NOISE.values()
    )
    changed = [f"{ROOT}/extraction/gemini.py"]
    after = with_blob(changed[0], "g2")
    head = three(gemini=a_row(field_f1=0.614, predictions="p2", listing=after))
    assert not check(three(), head, changed, after).passed


def test_a_stale_extraction_fails_for_the_row_a_backend_module_touches() -> None:
    changed = [f"{ROOT}/extraction/openai.py"]
    after = with_blob(changed[0], "o2")

    verdict = check(three(), three(), changed, after)

    assert not verdict.passed
    assert [(each.backend, each.paths) for each in verdict.stale] == [
        ("openai", "extraction")
    ]


def test_a_stale_extraction_fails_for_every_row_a_shared_file_touches() -> None:
    changed = [f"{ROOT}/extraction/extractor.py"]
    after = with_blob(changed[0], "a2")
    head = three(azure=a_row("azure", predictions="p2", listing=after))

    verdict = check(three(), head, changed, after)

    assert [(each.backend, each.paths) for each in verdict.stale] == [
        ("gemini", "extraction"),
        ("openai", "extraction"),
    ]


def test_a_stale_score_fails_for_every_row() -> None:
    changed = [f"{ROOT}/gate.py"]
    after = with_blob(changed[0], "q2")
    head = three(gemini=a_row(listing=after))

    verdict = check(three(), head, changed, after)

    assert [(each.backend, each.paths) for each in verdict.stale] == [
        ("azure", "scoring"),
        ("openai", "scoring"),
    ]


def test_the_label_does_not_pass_a_stale_aggregate() -> None:
    changed = [f"{ROOT}/gate.py"]
    after = with_blob(changed[0], "q2")
    verdict = check(
        three(),
        three(),
        changed,
        after,
        labeled=True,
        body="## Regression reason\n\nWhy.",
    )
    assert not verdict.passed


def dropped() -> tuple[Aggregate, list[str], tuple[tuple[str, str], ...]]:
    changed = [f"{ROOT}/metrics/fields.py"]
    after = with_blob(changed[0], "f2")
    head = {b: a_row(b, listing=after) for b in EXTRACTION_NOISE}
    head["gemini"] = a_row(field_f1=0.5, listing=after)
    return head, changed, after


def test_the_label_with_a_reason_passes_a_regression_and_still_lists_it() -> None:
    head, changed, after = dropped()
    body = "Summary.\n\n## Regression reason\n\nThe trade buys recall.\n\n## Test\n"

    verdict = check(three(), head, changed, after, labeled=True, body=body)

    assert verdict.passed
    assert verdict.accepted
    assert [each.backend for each in verdict.regressions] == ["gemini"]


def test_the_label_without_a_reason_does_not_pass() -> None:
    head, changed, after = dropped()
    verdict = check(three(), head, changed, after, labeled=True, body="Summary.")
    assert not verdict.passed
    assert not verdict.accepted


def test_a_reason_without_the_label_does_not_pass() -> None:
    head, changed, after = dropped()
    body = "## Regression reason\n\nWhy."
    assert not check(three(), head, changed, after, body=body).passed


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("## Regression reason\n\nThe trade buys recall.", True),
        ("### regression reason\nWhy.\n", True),
        ("## Regression reason\n\n## Test\nran it", False),
        ("## Regression reason\n   \n", False),
        ("Regression reason: none", False),
        ("", False),
    ],
)
def test_a_reason_section_is_a_heading_with_text_under_it(
    body: str, reason: bool
) -> None:
    assert has_reason(body) is reason


def test_a_row_removed_from_the_aggregate_is_a_regression() -> None:
    head = three()
    del head["openai"]
    verdict = check(three(), head, [], LISTING)
    assert [(each.backend, each.kind) for each in verdict.regressions] == [
        ("openai", "removed"),
        ("openai", "removed"),
    ]


def test_a_backends_first_row_has_nothing_to_regress_from() -> None:
    base = three()
    del base["openai"]
    verdict = check(base, three(), [], LISTING)
    assert verdict.passed
    assert verdict.comparisons[-1].kind == "first row"


def test_an_aggregate_born_in_the_pr_passes_without_freshness() -> None:
    """The baseline is born from the README's rows, not re-extracted (#153)."""
    changed = [f"{ROOT}/extraction/run.py"]
    after = with_blob(changed[0], "r2")

    verdict = check(None, three(), changed, after)

    assert verdict.born
    assert verdict.passed
    assert not verdict.stale
    assert {each.kind for each in verdict.comparisons} == {"first row"}


def test_no_aggregate_on_either_side_passes() -> None:
    verdict = check(None, None, [], LISTING)
    assert verdict.passed
    assert verdict.comparisons == ()


# Reading and writing.


def test_writes_one_backends_row_and_keeps_the_others(tmp_path: Path) -> None:
    path = tmp_path / "benchmark" / "aggregate.json"
    write_row(path, "openai", a_row("openai"))
    write_row(path, "gemini", a_row("gemini"))
    write_row(path, "openai", a_row("openai", field_f1=0.6))

    written = read_aggregate(path.read_text())

    assert list(written) == ["gemini", "openai"]
    assert written["openai"].field_f1 == 0.6
    assert written["gemini"] == a_row("gemini")


def test_an_aggregate_holds_numbers_and_digests_only(tmp_path: Path) -> None:
    path = tmp_path / "aggregate.json"
    write_row(path, "gemini", a_row())
    assert json.loads(path.read_text())["gemini"] == {
        "requested_model": "model",
        "field_f1": 0.615,
        "line_item_f1": 0.374,
        "predictions": "p1",
        "extracted_on": {"commit": "c1", "tree": extraction_tree(LISTING, "gemini")},
        "scored_on": {"commit": "c1", "tree": scoring_tree(LISTING)},
    }


def test_a_malformed_aggregate_is_a_message() -> None:
    with pytest.raises(RegressionError, match="not an aggregate"):
        read_aggregate('{"gemini": {"field_f1": "high"}}')


def test_reads_the_label_and_the_body_from_a_pull_request_event() -> None:
    event = {
        "pull_request": {
            "labels": [{"name": "enhancement"}, {"name": "regression-accepted"}],
            "body": "## Regression reason\n\nWhy.",
        }
    }
    assert read_event(json.dumps(event)) == (True, "## Regression reason\n\nWhy.")


def test_a_pull_request_with_no_body_and_no_label() -> None:
    event = '{"pull_request": {"labels": [], "body": null}}'
    assert read_event(event) == (False, "")


# Git, on a repository made for the test.


def test_lists_the_tree_of_a_commit(repo: Path) -> None:
    listed = dict(listing(repo, "HEAD"))
    assert set(listed) == {f"{ROOT}/gate.py", "README.md"}
    assert listed["README.md"] == git(repo, "rev-parse", "HEAD:README.md")


def test_lists_the_paths_changed_since_a_commit(repo: Path) -> None:
    first = git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("two\n")
    git(repo, "commit", "--quiet", "-am", "second")
    assert changed_paths(repo, first, "HEAD") == ["README.md"]


def test_shows_a_file_at_a_commit_or_nothing(repo: Path) -> None:
    assert show(repo, "HEAD", "README.md") == "readme\n"
    assert show(repo, "HEAD", "benchmark/aggregate.json") is None


def test_names_uncommitted_paths_in_a_list(repo: Path) -> None:
    assert dirty_paths(repo, scoring_path) == ()
    (repo / f"{ROOT}/gate.py").write_text("two\n")
    (repo / f"{ROOT}/metrics").mkdir()
    (repo / f"{ROOT}/metrics/new.py").write_text("new\n")
    (repo / "README.md").write_text("two\n")
    assert dirty_paths(repo, scoring_path) == (
        f"{ROOT}/gate.py",
        f"{ROOT}/metrics/new.py",
    )


def test_a_git_failure_is_a_message(repo: Path) -> None:
    with pytest.raises(RegressionError, match="git"):
        listing(repo, "no-such-commit")


# A row measured on a checkout.


def measure(repo: Path, commit: str | None, *, dirty: bool = False) -> Row:
    predictions = repo / "predictions.json"
    predictions.write_text("{}\n")
    return measured_row(
        repo,
        "gemini",
        requested_model="model",
        extracted_on=commit,
        dirty=dirty,
        field_f1=0.5,
        line_item_f1=0.25,
        predictions=predictions,
    )


def test_a_row_carries_its_trees_at_the_run_and_at_the_eval(repo: Path) -> None:
    extracted = git(repo, "rev-parse", "HEAD")
    (repo / f"{ROOT}/gate.py").write_text("two\n")
    git(repo, "commit", "--quiet", "-am", "second")
    scored = git(repo, "rev-parse", "HEAD")

    row = measure(repo, extracted)

    assert row.extracted_on == Stamp(
        commit=extracted, tree=extraction_tree(listing(repo, extracted), "gemini")
    )
    assert row.scored_on == Stamp(
        commit=scored, tree=scoring_tree(listing(repo, scored))
    )
    assert (row.field_f1, row.line_item_f1) == (0.5, 0.25)
    assert row.predictions == hashlib.sha256(b"{}\n").hexdigest()


def test_a_run_with_no_commit_is_refused(repo: Path) -> None:
    with pytest.raises(RegressionError, match="records no commit"):
        measure(repo, None)


def test_a_run_extracted_from_uncommitted_code_is_refused(repo: Path) -> None:
    with pytest.raises(RegressionError, match="uncommitted"):
        measure(repo, git(repo, "rev-parse", "HEAD"), dirty=True)


def test_a_run_whose_commit_is_not_here_is_refused(repo: Path) -> None:
    with pytest.raises(RegressionError, match="git"):
        measure(repo, "0" * 40)


def test_scoring_from_uncommitted_scoring_code_is_refused(repo: Path) -> None:
    (repo / f"{ROOT}/gate.py").write_text("two\n")
    with pytest.raises(RegressionError, match=f"{ROOT}/gate.py"):
        measure(repo, git(repo, "rev-parse", "HEAD"))
