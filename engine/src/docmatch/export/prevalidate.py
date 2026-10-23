"""DGII's own pre-validator run over the 606 files an export wrote (#215, #217).

The pre-validator is DGII's `Pre_Validado.zip`, never committed: it is pinned
by SHA-256, and a file of any other hash stops the command before anything of
it runs. It runs under Mono in the container beside this module
(`prevalidator/`), with no network, driven by a C# harness that types the
reporter and the period, hands the form the file and prints what the form
shows. A file passes when the form finds zero errors in it; the status line
reads `Proceso Completado` on a pass and on a failure alike, so it only says
the form finished.

The number, per side (#215 decision 2): N of M monthly files with zero
errors, and rows with an error out of all rows. A month `export 606` already
failed (a document with no reading, or without NCF, RNC or date, or the "no
month" row) counts as failed without running the pre-validator, and its
failing documents as rows with an error. The other rows of such a month were
withheld with it and never pre-validated, so they count among all rows and
are printed apart, never as passing.

Nothing printed quotes a comprobante: the form's messages quote the values
they reject, so every digit in a message is printed as `#`.
"""

import contextlib
import hashlib
import json
import re
import subprocess
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from docmatch.export.dgii606 import EXPORT_FILE, NOT_REPORTED

BINARY_SHA256 = "130affffbdb3f2f3a6e344564d47f88f1d7381436e7ed8c57cba299504a091e6"
"""`Pre_Validado.zip` as DGII published it on 2026-07-31, read on #207 and
#217 and downloaded again on 2026-10-23: MSI 2.6, `DGII.PreValidado.exe`
assembly 4.0.0.9."""

MSI_VERSION = "2.6"
EXE_VERSION = "4.0.0.9"
RUNTIME = "Mono 6.12.0.182"

COMPLETED = "Proceso Completado"
"""The form's status once it has checked a file, whatever it found."""

CONTAINER = Path(__file__).parent / "prevalidator"
"""The Dockerfile, the harness and the script the container runs."""

HASH_REFUSED = 64
"""The script's exit code when the mounted zip is not the pinned one."""

ENCODING = "utf-8"
"""What the harness prints in: it sets Mono's console to UTF-8 itself, so the
form's accented messages read the same whatever the host's locale."""

TIMEOUT_SECONDS = 300
"""Longest one file's run may take; the harness gives the form 120 s."""

BUILD_TIMEOUT_SECONDS = 1800
"""Longest the image build may take: a cold build installs Mono's packages
from archive.debian.org, far slower than one file's run."""

SIDES = (("reading", "Reading"), ("control", "Labels control"))


class PrevalidateError(Exception):
    """A pre-validator, an export or a harness answer the command cannot use."""


def check_binary(binary: Path, pinned: str = BINARY_SHA256) -> None:
    """Refuse any `Pre_Validado.zip` but the pinned one: the harness reads the
    form's private members by name, and a republished build can move them,
    or move the number silently."""
    if not binary.is_file():
        raise PrevalidateError(
            f"{binary} is not a file; download DGII's Pre_Validado.zip there"
        )
    found = hashlib.sha256(binary.read_bytes()).hexdigest()
    if found != pinned:
        raise PrevalidateError(
            f"{binary} has SHA-256 {found}, and the pre-validator this command "
            f"drives is pinned at {pinned}; a different build is not run"
        )


# The harness's verdict.

_LINE = re.compile(r"\bLinea:\s*(\d+)")
_NUMBERED = re.compile(r"^\s*(\d+)\.\s*(.*\S)\s*$")


@dataclass(frozen=True)
class Verdict:
    """What the form found in one file: its errors, none on a pass."""

    errors: tuple[str, ...]
    """Each error line as the form numbered it, its number taken off."""

    @property
    def passed(self) -> bool:
        return not self.errors

    def rows(self) -> frozenset[int]:
        """The detail lines the errors name, counted from 1 after the
        header; an error on the header names none."""
        return frozenset(
            int(found.group(1))
            for error in self.errors
            if (found := _LINE.search(error)) is not None
        )


def read_verdict(output: str) -> Verdict:
    """The verdict in the harness's printed block.

    Pass is an error count of zero, blank as the form shows it, with an empty
    error box; the count and the box's numbered lines must agree, and a form
    that never reached `COMPLETED` has no verdict at all.
    """
    lines = output.splitlines()
    status = _field(lines, "status")
    if status != COMPLETED:
        raise PrevalidateError(
            f"the pre-validator did not finish checking the file (status {status!r})"
        )
    count_text = _field(lines, "errors")
    if count_text != "" and not count_text.isdigit():
        raise PrevalidateError(f"the error count {count_text!r} is not a number")
    count = int(count_text or "0")
    if "error box:" not in lines:
        raise PrevalidateError("the harness printed no 'error box' line")
    box = lines[lines.index("error box:") + 1 :]
    errors = tuple(
        found.group(2) for line in box if (found := _NUMBERED.match(line)) is not None
    )
    if len(errors) != count:
        raise PrevalidateError(
            f"the form counts {count} errors and lists {len(errors)}; "
            "the harness's reading of it no longer holds"
        )
    return Verdict(errors)


def _field(lines: Sequence[str], name: str) -> str:
    prefix = f"{name}:"
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise PrevalidateError(f"the harness printed no {name!r} line")


# Running it over an export.

Runner = Callable[[Path, str, str], str]
"""Runs the pre-validator over one 606 file, typed with its reporter and
period, and returns what the harness printed."""


@dataclass(frozen=True)
class Checked:
    """One month of one side, and what came of it."""

    period: str
    rows: int
    """Every document of the month, written or not."""
    rows_with_error: int
    withheld: int
    """Rows of a month the export failed, never pre-validated."""
    verdict: Verdict | None
    """None when the export failed the month and nothing ran."""
    export_reasons: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.verdict is not None and self.verdict.passed


@dataclass(frozen=True)
class Checks:
    """One side's months, the reading's or the labels control's."""

    heading: str
    months: tuple[Checked, ...]

    @property
    def passed(self) -> int:
        return sum(month.passed for month in self.months)

    @property
    def rows(self) -> int:
        return sum(month.rows for month in self.months)

    @property
    def rows_with_error(self) -> int:
        return sum(month.rows_with_error for month in self.months)

    @property
    def withheld(self) -> int:
        return sum(month.withheld for month in self.months)


@dataclass(frozen=True)
class Prevalidation:
    """Both sides of an export, pre-validated."""

    export: Path
    not_reported: int
    sides: tuple[Checks, ...]


def prevalidate(export: Path, run: Runner) -> Prevalidation:
    """Pre-validate every file `export 606` wrote under `export`, as its
    `export.json` lists them, month by month and side by side."""
    listed = _read_export(export)
    sides = []
    for key, heading in SIDES:
        months = listed.get(key)
        if not isinstance(months, list):
            raise PrevalidateError(f"{export / EXPORT_FILE} lists no {key} side")
        sides.append(
            Checks(heading, tuple(_check(export, month, run) for month in months))
        )
    not_reported = listed.get("not_reported", [])
    if not isinstance(not_reported, list):
        raise PrevalidateError(f"{export / EXPORT_FILE} has a bad not_reported list")
    return Prevalidation(export, len(not_reported), tuple(sides))


def _read_export(export: Path) -> Mapping[str, object]:
    path = export / EXPORT_FILE
    try:
        listed = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise PrevalidateError(
            f"{path} does not exist; give the directory `export 606 --out` wrote"
        ) from error
    except json.JSONDecodeError as error:
        raise PrevalidateError(f"{path} is not JSON: {error}") from error
    if not isinstance(listed, dict):
        raise PrevalidateError(f"{path} is not an object")
    return listed


def _check(export: Path, month: object, run: Runner) -> Checked:
    if not isinstance(month, dict):
        raise PrevalidateError(f"{export / EXPORT_FILE} lists a month that is not one")
    period, file, rows, failures = (
        month.get("period"),
        month.get("file"),
        month.get("rows"),
        month.get("failures"),
    )
    if (
        not isinstance(period, str)
        or not isinstance(rows, int)
        or isinstance(rows, bool)
        or rows < 0
        or not isinstance(failures, list)
        or not (file is None or isinstance(file, str))
    ):
        raise PrevalidateError(f"{export / EXPORT_FILE} lists month {period!r} badly")
    if file is None:
        reasons = tuple(
            reason
            for failure in failures
            if isinstance(failure, dict)
            for reason in failure.get("reasons", [])
            if isinstance(reason, str)
        )
        return Checked(
            period=period,
            rows=rows + len(failures),
            rows_with_error=len(failures),
            withheld=rows,
            verdict=None,
            export_reasons=reasons,
        )
    path = _inside(export, file)
    reporter, typed_period, count = _header(path)
    if typed_period != period or count != rows:
        raise PrevalidateError(
            f"{path}'s header does not match the month {export / EXPORT_FILE} lists"
        )
    verdict = read_verdict(run(path, reporter, period))
    named = verdict.rows()
    if any(row < 1 or row > rows for row in named):
        raise PrevalidateError(f"the pre-validator names a line {path} does not have")
    return Checked(
        period=period,
        rows=rows,
        rows_with_error=len(named),
        withheld=0,
        verdict=verdict,
        export_reasons=(),
    )


def _inside(export: Path, file: str) -> Path:
    """The listed file, refused unless it is a relative path that stays
    inside the export directory, the way `export 606` writes it."""
    listed = PurePosixPath(file)
    if listed.is_absolute() or ".." in listed.parts or not listed.parts:
        raise PrevalidateError(
            f"{export / EXPORT_FILE} lists {file!r}, which is not a file inside "
            "the export directory"
        )
    return export / listed


def _header(path: Path) -> tuple[str, str, int]:
    """The reporter, the period and the line count a 606's header gives."""
    try:
        first = path.read_bytes().split(b"\n", 1)[0].decode("ascii").strip()
    except FileNotFoundError as error:
        raise PrevalidateError(f"{path} is listed and does not exist") from error
    fields = first.split("|")
    if len(fields) != 4 or fields[0] != "606" or not fields[3].isdigit():
        raise PrevalidateError(f"{path} does not start with a 606 header")
    return fields[1], fields[2], int(fields[3])


def container_runner(binary: Path) -> Runner:
    """A runner that builds the pinned image, then runs each file in its own
    container with no network, the zip and the file mounted read-only.

    The build is the only step that reaches the network, for the image's
    packages; nothing of DGII's is in the image.
    """
    check_binary(binary)
    context = hashlib.sha256(
        b"".join(
            (CONTAINER / name).read_bytes()
            for name in ("Dockerfile", "Harness.cs", "prevalidate.sh")
        )
    ).hexdigest()
    image = f"docmatch-prevalidator:{context[:12]}"
    _docker(
        ["build", "--quiet", "--tag", image, str(CONTAINER)],
        "build the image",
        timeout=BUILD_TIMEOUT_SECONDS,
    )

    def run(path: Path, reporter: str, period: str) -> str:
        return run_in_container(binary, image, path, reporter, period)

    return run


def run_in_container(
    binary: Path, image: str, path: Path, reporter: str, period: str
) -> str:
    """One file's run, in a container of its own name with no network, the
    zip and the file's directory bind-mounted read-only. A run that times out
    or is interrupted has its container removed: the timeout stops only the
    docker CLI, and the container would go on running without it."""
    sources = (binary.resolve(), path.parent.resolve())
    if any("," in str(source) for source in sources):
        raise PrevalidateError(
            f"{binary} or {path.parent} holds a comma, which `docker run "
            "--mount` reads as the end of its source; move it to a path without one"
        )
    name = f"docmatch-prevalidate-{uuid.uuid4().hex[:12]}"
    done = _docker(
        [
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--mount",
            f"type=bind,source={sources[0]},target=/dgii/Pre_Validado.zip,readonly",
            "--mount",
            f"type=bind,source={sources[1]},target=/files,readonly",
            image,
            BINARY_SHA256,
            reporter,
            period,
            f"/files/{path.name}",
        ],
        f"pre-validate {path}",
        check=False,
        container=name,
    )
    stderr = done.stderr.decode(ENCODING, errors="replace").strip()
    if done.returncode == HASH_REFUSED:
        raise PrevalidateError(f"the container refused {binary}: {stderr}")
    try:
        stdout = done.stdout.decode(ENCODING)
    except UnicodeDecodeError as error:
        raise PrevalidateError(
            f"the harness's output on {path} is not UTF-8: {error}"
        ) from error
    if done.returncode != 0:
        raise PrevalidateError(
            f"the pre-validator exited {done.returncode} on {path}: "
            f"{stdout.strip()[-400:]} {stderr[-400:]}"
        )
    return stdout


def _docker(
    arguments: list[str],
    doing: str,
    check: bool = True,
    timeout: int = TIMEOUT_SECONDS,
    container: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """The docker CLI, its output left as bytes; `container`, when given, is
    removed if the CLI times out or is interrupted."""
    try:
        done = subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise PrevalidateError("docker is not installed or not on PATH") from error
    except subprocess.TimeoutExpired as error:
        _remove(container)
        raise PrevalidateError(f"docker took over {timeout} s to {doing}") from error
    except KeyboardInterrupt:
        _remove(container)
        raise
    if check and done.returncode != 0:
        stderr = done.stderr.decode(ENCODING, errors="replace").strip()
        raise PrevalidateError(f"docker could not {doing}: {stderr[-400:]}")
    return done


def _remove(container: str | None) -> None:
    """Remove a container left running, quietly: it may never have started."""
    if container is None:
        return
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            ["docker", "rm", "-f", container],
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )


# The report.


def render_prevalidation(result: Prevalidation) -> str:
    """The number for each side, then each month: counts, periods, the
    export's reasons and the form's messages with every digit hidden."""
    lines = [
        "606 pre-validation",
        f"  export        {result.export}",
        f"  binary        Pre_Validado.zip sha256 {BINARY_SHA256}",
        f"  version       MSI {MSI_VERSION}, exe {EXE_VERSION}",
        f"  runtime       {RUNTIME}, a pass uncompared with one on Windows",
        f"  not reported  {result.not_reported} ({NOT_REPORTED})",
        "",
    ]
    for side in result.sides:
        lines.append(
            f"{side.heading}, {side.passed} of {len(side.months)} monthly files "
            f"with zero errors, {side.rows_with_error} of {side.rows} "
            f"{_plural(side.rows, 'row')} with an error"
        )
        width = max((len(month.period) for month in side.months), default=0)
        for month in side.months:
            lines.append(f"  {month.period:<{width}}  {_month(month)}")
        if side.withheld:
            lines.append(
                f"  {side.withheld} {_plural(side.withheld, 'row')} of failed months "
                "withheld by the export, not pre-validated"
            )
        lines.append("")
    return "\n".join(lines)


def _month(month: Checked) -> str:
    if month.verdict is None:
        failed = month.rows_with_error
        return (
            f"failed by the export, {failed} {_plural(failed, 'document')}: "
            f"{_tally(month.export_reasons)}"
        )
    rows = f"{month.rows} {_plural(month.rows, 'row')}"
    if month.verdict.passed:
        return f"0 errors, {rows}"
    count = len(month.verdict.errors)
    messages = _tally(tuple(_hidden(error) for error in month.verdict.errors))
    errors = f"{count} {_plural(count, 'error')}"
    on_header = any(_LINE.search(error) is None for error in month.verdict.errors)
    if not on_header:
        return f"{errors} on {month.rows_with_error} of {rows}: {messages}"
    if month.rows_with_error == 0:
        return f"{errors} on the header, none on its {rows}: {messages}"
    return f"{errors} on the header and {month.rows_with_error} of {rows}: {messages}"


def _hidden(error: str) -> str:
    """The form's message without the line it names or any digit it quotes."""
    message = re.sub(r"^.*?\bLinea:\s*\d+\s*:?\s*", "", error)
    return re.sub(r"\d", "#", message)


def _tally(reasons: Sequence[str]) -> str:
    counted = Counter(reasons)
    return "; ".join(
        reason if count == 1 else f"{reason} x{count}"
        for reason, count in counted.items()
    )


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"
