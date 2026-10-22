"""Tests for the comparison `docmatch reproduce` makes: a computed per-type
table against a committed half, cell by cell and exactly."""

from pathlib import Path

import pytest

from docmatch import cli
from docmatch.cli import main
from docmatch.conftest import git
from docmatch.reproduce import (
    Difference,
    Half,
    ReproduceError,
    TypeCells,
    compare,
    read_tables,
    write_half,
)


def a_half(**changes: object) -> Half:
    half = Half(
        per_type={
            "price variance": TypeCells(
                precision=0.984, recall=0.991, n=1165, documents=4956
            ),
            "missing line": TypeCells(
                precision=1.0, recall=1.0, n=1930, documents=5325
            ),
        },
        clean_case_false_positive_rate=0.007,
        clean_cases=1000,
        clean_false_positives=7,
        seed=20260919,
        produced_on="08d14ca",
    )
    return half.model_copy(update=changes)


def test_an_identical_table_has_no_difference() -> None:
    assert compare(a_half(), a_half()) == ()


def test_the_commit_a_half_was_produced_on_is_no_cell() -> None:
    """A rerun on a later commit that computes the same cells matches."""
    assert compare(a_half(), a_half(produced_on="c5b81aa")) == ()


def test_a_cell_off_in_its_last_bit_is_a_difference() -> None:
    """The comparison is exact: the draw is pinned, so any change is a finding."""
    computed = a_half()
    computed.per_type["price variance"] = TypeCells(
        precision=0.984 + 1e-15, recall=0.991, n=1165, documents=4956
    )

    assert compare(a_half(), computed) == (
        Difference("price variance precision", 0.984, 0.984 + 1e-15),
    )


def test_every_differing_cell_is_listed_with_both_values() -> None:
    computed = a_half(clean_false_positives=8, clean_case_false_positive_rate=0.008)
    computed.per_type["missing line"] = TypeCells(
        precision=1.0, recall=0.999, n=1931, documents=5325
    )

    assert compare(a_half(), computed) == (
        Difference("missing line recall", 1.0, 0.999),
        Difference("missing line n", 1930, 1931),
        Difference("clean-case false-positive rate", 0.007, 0.008),
        Difference("clean cases with a finding", 7, 8),
    )


def test_a_different_seed_is_a_difference() -> None:
    assert compare(a_half(), a_half(seed=1)) == (Difference("seed", 20260919, 1),)


def test_a_type_on_one_side_only_is_a_difference() -> None:
    computed = a_half()
    del computed.per_type["missing line"]

    assert compare(a_half(), computed) == (
        Difference("missing line", "in the aggregate", "not computed"),
    )


def test_a_written_half_reads_back_beside_the_other(tmp_path: Path) -> None:
    path = tmp_path / "benchmark" / "matching.json"
    write_half(path, "synthetic", a_half(seed=1))
    write_half(path, "docile", a_half())

    tables = read_tables(path)

    assert list(tables) == ["docile", "synthetic"]
    assert tables["docile"] == a_half()
    assert tables["synthetic"] == a_half(seed=1)


def test_an_unreadable_aggregate_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "matching.json"
    path.write_text('{"docile": {"seed": "x"}}')

    with pytest.raises(ReproduceError, match="not a matching aggregate"):
        read_tables(path)


def test_a_missing_aggregate_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ReproduceError, match="no matching aggregate"):
        read_tables(tmp_path / "matching.json")


# Through the command line, on the committed fixture (seam 1).

COMMITTED = Path(__file__).parents[3] / "benchmark" / "matching.json"

FIXTURE_TABLE = "\n".join(
    [
        "Per-type table, over cases from train and val",
        "  type            precision  recall     n  documents",
        "  price variance      1.000   1.000  1050          6",
        "  short-ship          1.000   1.000   931          5",
        "  over-ship           1.000   1.000   980          5",
        "  extra line          1.000   0.998  1005          6",
        "  missing line        1.000   0.999  1628          6",
        "  unit variant        1.000   1.000   798          2",
        "  tax mismatch        1.000   1.000   590          1",
        "  clean-case false-positive rate  0.000 of 1000 cases",
        "",
    ]
)


def test_reproduce_on_the_fixture_matches_the_committed_half(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No --data-dir: the synthetic half reads the committed fixture."""
    produced_on = read_tables(COMMITTED)["synthetic"].produced_on

    exit_code = main(["reproduce", "synthetic", "--tables", str(COMMITTED)])

    assert capsys.readouterr().out == (
        f"{FIXTURE_TABLE}\n"
        f"Matches the README: every cell equals the synthetic half of "
        f"{COMMITTED}, produced at {produced_on[:7]} with seed 20260919\n"
    )
    assert exit_code == 0


def test_reproduce_lists_every_differing_cell_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tables = read_tables(COMMITTED)
    altered = tables["synthetic"]
    altered.per_type["price variance"] = altered.per_type["price variance"].model_copy(
        update={"n": 1049}
    )
    altered.clean_false_positives = 3
    path = tmp_path / "matching.json"
    write_half(path, "synthetic", altered)

    exit_code = main(["reproduce", "synthetic", "--tables", str(path)])

    out = capsys.readouterr().out
    assert out.startswith(FIXTURE_TABLE)
    assert out.endswith(
        "\n".join(
            [
                f"Differs from the synthetic half of {path}, produced at "
                f"{altered.produced_on[:7]} with seed 20260919, in 2 cells",
                "  cell                        committed  computed",
                "  price variance n            1049       1050",
                "  clean cases with a finding  3          0",
                "",
            ]
        )
    )
    assert exit_code == 1


def test_reproduce_reports_a_half_the_aggregate_lacks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "matching.json"
    write_half(path, "docile", a_half())

    exit_code = main(["reproduce", "synthetic", "--tables", str(path)])

    assert exit_code == 1
    assert "has no synthetic half" in capsys.readouterr().err


def test_reproduce_writes_the_half_on_the_current_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "SOURCE", repo)
    path = repo / "benchmark" / "matching.json"

    exit_code = main(["reproduce", "synthetic", "--write", "--tables", str(path)])

    commit = git(repo, "rev-parse", "HEAD")
    assert exit_code == 0
    assert capsys.readouterr().out == (
        f"{FIXTURE_TABLE}\n"
        f"Wrote the synthetic half to {path}, produced at {commit[:7]} "
        "with seed 20260919\n"
    )
    written = read_tables(path)["synthetic"]
    assert written.produced_on == commit
    assert written.per_type["extra line"].n == 1005
    assert main(["reproduce", "synthetic", "--tables", str(path)]) == 0


def test_reproduce_refuses_to_write_from_uncommitted_engine_code(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A half names the commit its cells came from, so the code must be it."""
    monkeypatch.setattr(cli, "SOURCE", repo)
    (repo / "engine/src/docmatch/gate.py").write_text("two\n")
    path = repo / "benchmark" / "matching.json"

    exit_code = main(["reproduce", "synthetic", "--write", "--tables", str(path)])

    assert exit_code == 1
    assert "engine/src/docmatch/gate.py" in capsys.readouterr().err
    assert not path.exists()
