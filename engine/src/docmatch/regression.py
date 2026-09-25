"""The regression gate: a pull request may not lower field F1 or line-item F1.

The numbers come from the fixed subset, which never reaches a CI runner (#25),
so CI cannot compute them. What it can do is compare. The local eval writes an
aggregate, field F1 and line-item F1 per backend with the digests that say
where they came from, and the pull request commits it. CI compares the
pull request's aggregate with the one at the merge base, which is the
baseline, so an improving pull request raises the bar for the next (#153).

CI cannot prove a number was computed honestly. It can prove it is fresh:
that the code the pull request carries is the code the number came from.

Two path lists
--------------

What a pull request touches decides what it owes.

- Extraction paths, the extraction package except the derived-currency
  module: a backend's own module touches its row, any other file every row.
  A touched row must be re-extracted on a commit whose extraction tree for
  that backend equals the pull request's.
- Scoring paths, the metrics, the evals, derived currency, the gate and the
  DocILE loader: every row must be re-scored from its saved run on a commit
  whose scoring tree equals the pull request's, which costs nothing.
- Anything else, matching, resolution, the pipeline and this module among
  them, owes nothing: none of it moves either number.

Tests are in neither list. A test file, a `conftest.py` or a fixture changes
what is checked, never what is measured, and a list that counted them would
ask for a paid re-extraction to land a test.

A tree is a digest of the git blob ids of the files in its list at a commit,
so the aggregate carries the trees it was produced on and CI needs none of
the commits that produced them, only the pull request's own checkout.

What fails
----------

A row whose predictions are the baseline's was re-scored, and re-scoring a
saved reading is exact, so any drop fails. A row whose predictions changed was
re-extracted, and when the pull request owed that re-extraction it fails only
on a drop larger than that backend's extraction noise for that metric, the six
constants below. A re-extraction nobody owed is held to any drop, since beside
a scoring change it could hide a scoring drop inside the noise, and so is one
owed beside a re-score, for the same reason: land the scoring change first,
where the re-score is exact, and the re-extraction after it (#178). A row the
baseline has and the pull request dropped fails like a regression; a
backend's first row has nothing to regress from.

A stale row always fails. A regression passes only with the
`regression-accepted` label and a reason section in the pull request's body,
and it is printed either way, so a deliberate trade stays in the history
(the #102 precedent).

The first aggregate
-------------------

When neither the merge base nor the branch it merges into has an aggregate,
the pull request's is born: there is nothing to compare and nothing to be
fresh against. A merge base older than an aggregate the branch already holds
is no birth; the command asks for the branch to be merged in first, or every
change cut before the gate landed would pass unchecked. The born rows are the
README's, scored from the saved runs of the commits the README links
(`28d0738`, `d3bb01e`, `42e69fa`) without re-extracting them (#153).
"""

import hashlib
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from docmatch.extraction.backends import BACKENDS

AGGREGATE = Path("benchmark/aggregate.json")
"""Where the aggregate lives, from the repository root, outside both lists."""

LABEL = "regression-accepted"
"""The label that lets a reasoned regression through."""

REASON_HEADING = "Regression reason"
"""The heading of the section in the pull request's body that gives the reason."""

PACKAGE = "engine/src/docmatch"
EXTRACTION = f"{PACKAGE}/extraction"
DERIVED = f"{EXTRACTION}/derived.py"
OWN_MODULES = {backend: f"{EXTRACTION}/{backend}.py" for backend in BACKENDS}
"""Each backend's own module, which touches that backend's row alone."""

SCORING = (
    f"{PACKAGE}/metrics",
    f"{PACKAGE}/evals",
    DERIVED,
    f"{PACKAGE}/gate.py",
    f"{PACKAGE}/docile",
)
"""The scoring paths, files or directories."""

Metric = Literal["field_f1", "line_item_f1"]
METRICS: tuple[Metric, ...] = ("field_f1", "line_item_f1")


@dataclass(frozen=True)
class Noise:
    """How far one backend's two numbers move when nothing changed but the run."""

    field_f1: float
    line_item_f1: float

    def of(self, metric: Metric) -> float:
        return self.field_f1 if metric == "field_f1" else self.line_item_f1


EXTRACTION_NOISE: Mapping[str, Noise] = {
    "gemini": Noise(field_f1=0.0165, line_item_f1=0.1447),
    "azure": Noise(field_f1=0.0009, line_item_f1=0.0004),
    "openai": Noise(field_f1=0.0204, line_item_f1=0.0592),
}
"""Each backend's extraction noise, measured at `e299b77` (#178).

Procedure: the same 100 DocILE train documents (the noise subset below:
pinned seed, the fixed subset's admission rules, never the fixed subset
itself) re-extracted three times per backend; the noise is the largest
difference between any two of the three runs, for field F1 and line-item F1
apart, kept to the four places `docmatch noise` prints it at. Line items
move a document at a time, a whole table read right on one run and wrong on
the next, which is why the sampled models' line-item noise is wide.
"""


NOISE_SUBSET = Path(__file__).parent / "noise_subset.json"
"""The 100 train documents the extraction noise is measured over, drawn by
`docmatch subset --write --split train` with the fixed subset's seed and
admission, beside the code that reads it and outside both path lists."""

RUNS = 3
"""How many re-extractions of the noise subset each backend's noise is
measured over."""


class RegressionError(Exception):
    """An aggregate, an event or a git call the gate cannot read."""


def measured_noise(
    scores: Mapping[str, Sequence[tuple[float, float]]],
) -> dict[str, Noise]:
    """Each backend's extraction noise from its runs' field F1 and line-item
    F1: the largest difference between any two runs, which is the highest
    less the lowest, for each metric apart."""
    for backend, runs in scores.items():
        if len(runs) != RUNS:
            raise RegressionError(
                f"{backend} has {len(runs)} runs of the noise subset, and its "
                f"extraction noise is measured over {RUNS}"
            )
    return {
        backend: Noise(
            field_f1=_spread(field for field, _ in runs),
            line_item_f1=_spread(line_item for _, line_item in runs),
        )
        for backend, runs in scores.items()
    }


def _spread(values: Iterable[float]) -> float:
    kept = tuple(values)
    return max(kept) - min(kept)


# The two path lists.


def _is_test(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return name.startswith("test_") or name == "conftest.py" or "fixtures" in parts


def _within(path: str, where: str) -> bool:
    return path == where or path.startswith(f"{where}/")


def extraction_path(path: str, backend: str) -> bool:
    """Whether a change to this file demands this backend's row re-extracted."""
    if not _within(path, EXTRACTION) or path == DERIVED or _is_test(path):
        return False
    owner = next((b for b, module in OWN_MODULES.items() if module == path), None)
    return owner is None or owner == backend


def scoring_path(path: str) -> bool:
    """Whether a change to this file demands every row re-scored."""
    return not _is_test(path) and any(_within(path, each) for each in SCORING)


@dataclass(frozen=True)
class Demand:
    """What a pull request owes for the paths it touches."""

    re_extract: frozenset[str]
    """The backends whose rows must be re-extracted."""
    re_score: bool
    """Whether every row must be re-scored."""


def demanded(changed: Iterable[str]) -> Demand:
    paths = tuple(changed)
    return Demand(
        re_extract=frozenset(
            backend
            for backend in BACKENDS
            if any(extraction_path(path, backend) for path in paths)
        ),
        re_score=any(scoring_path(path) for path in paths),
    )


# Trees.

Listing = Iterable[tuple[str, str]]
"""A commit's files: each path from the repository root and its git blob id."""


def _tree(listed: Listing, within: Callable[[str], bool]) -> str:
    lines = sorted(f"{path} {blob}" for path, blob in listed if within(path))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def extraction_tree(listed: Listing, backend: str) -> str:
    """The digest of every file in this backend's extraction list."""
    return _tree(listed, lambda path: extraction_path(path, backend))


def scoring_tree(listed: Listing) -> str:
    """The digest of every file in the scoring list."""
    return _tree(listed, scoring_path)


# The aggregate.


class Stamp(BaseModel):
    """Where a number came from: the commit, and the tree that matters of it."""

    commit: str
    tree: str


class Row(BaseModel):
    """One backend's two numbers on the fixed subset, and where they came from."""

    requested_model: str
    field_f1: float
    line_item_f1: float
    predictions: str
    """The SHA-256 of the saved run's predictions file: a new one is a
    re-extraction, the same one a re-score."""
    extracted_on: Stamp
    """The run's extraction commit and that backend's extraction tree at it."""
    scored_on: Stamp
    """The commit the eval ran on and the scoring tree at it."""

    def of(self, metric: Metric) -> float:
        return self.field_f1 if metric == "field_f1" else self.line_item_f1


Aggregate = dict[str, Row]
AGGREGATE_FORMAT = TypeAdapter(Aggregate)


def read_aggregate(text: str) -> Aggregate:
    try:
        return AGGREGATE_FORMAT.validate_json(text)
    except ValidationError as error:
        raise RegressionError(
            "not an aggregate: expected a JSON object keyed by backend, each "
            f"holding field_f1, line_item_f1 and where they came from. {error}"
        ) from error


def write_row(path: Path, backend: str, row: Row) -> None:
    """Set one backend's row, keep the others, in the backends' order."""
    rows = read_aggregate(path.read_text("utf-8")) if path.exists() else {}
    rows[backend] = row
    ordered = {name: rows[name] for name in _backends(rows, {})}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(AGGREGATE_FORMAT.dump_json(ordered, indent=2) + b"\n")


def _rank(backend: str) -> int:
    return BACKENDS.index(backend) if backend in BACKENDS else len(BACKENDS)


def measured_row(
    repo: Path,
    backend: str,
    *,
    requested_model: str,
    extracted_on: str | None,
    dirty: bool,
    field_f1: float,
    line_item_f1: float,
    predictions: Path,
) -> Row:
    """A row for numbers the eval just computed on this checkout, or why not.

    Refused when either half would lie about where the numbers came from: a
    run that records no commit or was extracted from uncommitted code, or an
    eval run from uncommitted scoring code.
    """
    if extracted_on is None:
        raise RegressionError(
            "the run records no commit, so its row could not be checked for "
            "freshness; re-extract it, or add the commit it was extracted on"
        )
    if dirty:
        raise RegressionError(
            f"the run was extracted from uncommitted {backend} extraction code, "
            f"so no commit is where its numbers came from"
        )
    uncommitted = dirty_paths(repo, scoring_path)
    if uncommitted:
        raise RegressionError(
            "the scoring code has uncommitted changes, so no commit is where "
            f"these numbers came from: {', '.join(uncommitted)}"
        )
    scored = commit_of(repo)
    return Row(
        requested_model=requested_model,
        field_f1=field_f1,
        line_item_f1=line_item_f1,
        predictions=hashlib.sha256(predictions.read_bytes()).hexdigest(),
        extracted_on=Stamp(
            commit=extracted_on,
            tree=extraction_tree(listing(repo, extracted_on), backend),
        ),
        scored_on=Stamp(commit=scored, tree=scoring_tree(listing(repo, scored))),
    )


# The comparison.

Kind = Literal["re-scored", "re-extracted", "first row", "removed"]


@dataclass(frozen=True)
class Comparison:
    """One backend's one metric, before and after."""

    backend: str
    metric: Metric
    before: float | None
    after: float | None
    kind: Kind
    noise: float
    """The extraction noise this row's change may drop by without being a
    regression: the backend's for an owed re-extraction, else zero."""

    @property
    def drop(self) -> float:
        if self.before is None or self.after is None:
            return 0.0
        return self.before - self.after

    @property
    def regressed(self) -> bool:
        # Rounded so a drop equal to the noise is not beyond it by a float's
        # last bit; F1 over the subset's counts moves in steps near 1e-4, so
        # twelve places keep every real drop.
        return self.kind == "removed" or round(self.drop, 12) > self.noise


@dataclass(frozen=True)
class Stale:
    """A row the pull request owes and did not produce on its own code."""

    backend: str
    paths: Literal["extraction", "scoring"]
    """Which path list the row is stale for."""
    recorded: str
    wanted: str


@dataclass(frozen=True)
class Verdict:
    """What the gate found: every comparison, every stale row, and whether
    the label and a reason let a regression through."""

    born: bool
    """Whether the merge base had no aggregate, so there was nothing to compare."""
    demand: Demand
    comparisons: tuple[Comparison, ...]
    stale: tuple[Stale, ...]
    labeled: bool
    reasoned: bool

    @property
    def regressions(self) -> tuple[Comparison, ...]:
        return tuple(each for each in self.comparisons if each.regressed)

    @property
    def accepted(self) -> bool:
        """Whether a regression, if there is one, carries the label and a reason."""
        return self.labeled and self.reasoned

    @property
    def passed(self) -> bool:
        return not self.stale and (not self.regressions or self.accepted)


def check(
    base: Aggregate | None,
    head: Aggregate | None,
    changed: Iterable[str],
    listed: Listing,
    *,
    labeled: bool = False,
    body: str = "",
    noise: Mapping[str, Noise] = EXTRACTION_NOISE,
) -> Verdict:
    """The pull request's aggregate against the merge base's, and its freshness.

    `changed` is what the pull request touches since the merge base, and
    `listed` the pull request's own tree.
    """
    demand = demanded(changed)
    after = head or {}
    born = base is None
    before = {} if base is None else base
    comparisons = tuple(
        _compare(
            backend,
            metric,
            before.get(backend),
            after.get(backend),
            noise if backend in demand.re_extract and not demand.re_score else {},
        )
        for backend in _backends(before, after)
        for metric in METRICS
    )
    return Verdict(
        born=born,
        demand=demand,
        comparisons=comparisons,
        stale=() if born else _stale(after, demand, tuple(listed)),
        labeled=labeled,
        reasoned=has_reason(body),
    )


def _backends(before: Aggregate, after: Aggregate) -> list[str]:
    names = {*before, *after}
    return sorted(names, key=lambda b: (_rank(b), b))


def _compare(
    backend: str,
    metric: Metric,
    before: Row | None,
    after: Row | None,
    noise: Mapping[str, Noise],
) -> Comparison:
    kind: Kind
    extraction_noise = 0.0
    if before is None:
        kind = "first row"
    elif after is None:
        kind = "removed"
    elif after.predictions != before.predictions:
        kind = "re-extracted"
        extraction_noise = noise[backend].of(metric) if backend in noise else 0.0
    else:
        kind = "re-scored"
    return Comparison(
        backend=backend,
        metric=metric,
        before=None if before is None else before.of(metric),
        after=None if after is None else after.of(metric),
        kind=kind,
        noise=extraction_noise,
    )


def _stale(
    after: Aggregate, demand: Demand, listed: tuple[tuple[str, str], ...]
) -> tuple[Stale, ...]:
    found: list[Stale] = []
    scoring = scoring_tree(listed)
    for backend in _backends({}, after):
        row = after[backend]
        if backend in demand.re_extract:
            wanted = extraction_tree(listed, backend)
            if row.extracted_on.tree != wanted:
                found.append(
                    Stale(backend, "extraction", row.extracted_on.tree, wanted)
                )
        if demand.re_score and row.scored_on.tree != scoring:
            found.append(Stale(backend, "scoring", row.scored_on.tree, scoring))
    return tuple(found)


_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")


def has_reason(body: str) -> bool:
    """Whether the body has a reason section with some text under its heading."""
    inside = False
    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading:
            if inside:
                return False
            inside = heading.group(1).casefold() == REASON_HEADING.casefold()
        elif inside and line.strip():
            return True
    return False


class _Label(BaseModel):
    name: str


class _PullRequest(BaseModel):
    labels: list[_Label] = []
    body: str | None = None


class _Event(BaseModel):
    pull_request: _PullRequest


def read_event(text: str) -> tuple[bool, str]:
    """Whether a pull request event carries the label, and its body."""
    try:
        pull = _Event.model_validate_json(text).pull_request
    except ValidationError as error:
        raise RegressionError(f"not a pull request event: {error}") from error
    return any(each.name == LABEL for each in pull.labels), pull.body or ""


# Git.


def _git(repo: Path, *arguments: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = getattr(error, "stderr", "") or ""
        raise RegressionError(
            f"git {' '.join(arguments)} failed: {stderr.strip() or error}"
        ) from error
    return done.stdout


def root(where: Path) -> Path:
    """The repository a path is in."""
    return Path(_git(where, "rev-parse", "--show-toplevel").strip())


def commit_of(repo: Path, revision: str = "HEAD") -> str:
    return _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").strip()


def merge_base(repo: Path, one: str, other: str) -> str:
    return _git(repo, "merge-base", one, other).strip()


def listing(repo: Path, commit: str) -> tuple[tuple[str, str], ...]:
    """Every file of a commit, from the repository root, with its blob id."""
    listed: list[tuple[str, str]] = []
    for line in _git(repo, "ls-tree", "-r", "--full-tree", "-z", commit).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            listed.append((path, meta.split()[2]))
    return tuple(listed)


def changed_paths(repo: Path, since: str, until: str) -> list[str]:
    """The paths that differ between two commits, from the repository root."""
    out = _git(repo, "diff", "--name-only", "-z", "--no-renames", since, until)
    return [path for path in out.split("\0") if path]


def show(repo: Path, commit: str, path: str) -> str | None:
    """A file's text at a commit, None when the commit has no such file."""
    if not _git(repo, "ls-tree", "--full-tree", commit, "--", path).strip():
        return None
    return _git(repo, "show", f"{commit}:{path}")


def dirty_paths(repo: Path, within: Callable[[str], bool]) -> tuple[str, ...]:
    """Paths in a list that differ from HEAD in the working tree, untracked included."""
    changed = _git(repo, "diff", "--name-only", "-z", "--no-renames", "HEAD")
    untracked = _git(
        repo, "ls-files", "--others", "--exclude-standard", "--full-name", "-z"
    )
    paths = {path for path in (changed + untracked).split("\0") if path}
    return tuple(sorted(path for path in paths if within(path)))
