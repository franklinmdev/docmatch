"""Record the loop working on the synthetic fixture, as an mp4.

    uv run python scripts/demo.py [--out out/demo]

Five beats, in order: `docmatch loop` uploads every synthetic case to a
server reading with the replay backend; the review page shows a document
routed to review with its routing reason; the reviewer corrects a value the
gate failed on and the gate reruns; the reviewer approves; and `docmatch
pipeline` prints the report. Zero vendor cost: replay answers from the
committed saved run under `engine/tests/evals/synthetic/runs/loop`.

Only the committed fixture ever reaches the page. Before anything starts,
every PDF the loop uploads is checked against the digest its manifest pins,
and the loop verifies them again before its first upload.

The recorder is agent-browser 0.39.1 (`record start` and `record stop`, read
in `agent-browser skills get core --full` 2026-10-22), which pipes Chrome's
screencast into ffmpeg; ffmpeg n9.0.2 then writes H.264 in yuv420p so any
player opens the file. The terminal is recorded the same way: each command's
real output is written into a page that types it out. Neither tool is a
Python dependency.

Everything the script starts it stops: the server, the review page, the
browser session, and the loop's two Postgres schemas. Ports are picked free,
so two worktrees can record at once. Needs Postgres (`DOCMATCH_DATABASE_URL`),
npm, ffmpeg and agent-browser on PATH. The mp4 lands in `--out`, under the
ignored `out/`, and is never committed.
"""

import argparse
import hashlib
import html
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql

from docmatch.resolution.store import resolve_database_url

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path("engine/tests/evals/synthetic")
RUN = FIXTURE / "runs/loop"
MANIFEST = RUN / "manifest.json"
COPIES = FIXTURE / "copies"
REVIEW = ROOT / "apps/review"
HOST = "127.0.0.1"
WIDTH, HEIGHT = 1440, 900
STARTUP = 180.0
"""Seconds a server gets to answer: the embedder loads and the catalog is
rebuilt before `serve` listens."""

ROUTED = 2
"""The fixture document the loop holds on its match (a short-ship)."""
CORRECTED = 3
"""The fixture document the gate fails: total 412.50 against 400.00 due."""
FIELD, VALUE = "Amount due", "412.50"


class DemoError(Exception):
    """A step that cannot go on; the message says which and why."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("out/demo"),
        help="where the loop run, the logs and the mp4 go (default: out/demo)",
    )
    arguments = parser.parse_args(argv)
    os.chdir(ROOT)
    try:
        video = record(arguments.out)
    except DemoError as error:
        print(f"demo: {error}", file=sys.stderr)
        return 1
    print(f"demo: wrote {video}")
    return 0


def record(out: Path) -> Path:
    """Run the five beats and return the mp4."""
    for tool in ("agent-browser", "ffmpeg", "ffprobe", "npm"):
        if shutil.which(tool) is None:
            raise DemoError(f"{tool} is not on PATH")
    verify_fixture()
    out.mkdir(parents=True, exist_ok=True)
    database_url = resolve_database_url(None)
    loop_out = out / "loop"
    # A clean state: an earlier run's loop output never reaches this one.
    shutil.rmtree(loop_out, ignore_errors=True)
    loop_command = [
        "docmatch", "loop", "--backend", "replay", "--run", str(RUN),
        "--data-dir", str(FIXTURE), "--manifest", str(MANIFEST),
        "--copies", str(COPIES), "--out", str(loop_out),
    ]  # fmt: skip
    looped = docmatch(loop_command)
    schema = json.loads((loop_out / "loop.json").read_text())["database_schema"]
    try:
        report_command = ["docmatch", "pipeline", "--run", str(loop_out)]
        reported = docmatch(report_command)
        terminal(
            out / "loop.html",
            "1  Upload: each synthetic invoice goes in over POST /documents with "
            "its order and receipt, read by replay, no vendor call",
            loop_command,
            looped,
        )
        terminal(
            out / "pipeline.html",
            "5  The report: latency and cost per status, and the routing ladder",
            report_command,
            reported,
        )
        api, page = free_port(), free_port()
        with (
            started(serve_command(schema, api), out / "serve.log"),
            review_page(api, page, out),
        ):
            wait_for(f"http://{HOST}:{api}/documents/0", "docmatch serve", {404})
            wait_for(f"http://{HOST}:{page}/", "the review page", {200})
            raw = out / "raw.mp4"
            browse(out, page, raw)
    finally:
        drop(database_url, schema)
    video = out / "docmatch-demo.mp4"
    encode(raw, video)
    return video


def verify_fixture() -> None:
    """Every PDF the loop can upload is the committed fixture's, by digest."""
    pinned = json.loads(MANIFEST.read_text())["digests"]
    for document, expected in pinned.items():
        pdf = COPIES / f"{document}.pdf"
        found = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if found != expected:
            raise DemoError(f"{pdf} is not the fixture's copy ({found})")


def docmatch(command: Sequence[str]) -> str:
    """A docmatch command's output, run the way `uv run docmatch` runs it."""
    done = subprocess.run(
        [sys.executable, "-m", *command],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise DemoError(f"`{' '.join(command)}` failed:\n{done.stderr}")
    return done.stdout


def serve_command(schema: str, port: int) -> list[str]:
    """`docmatch serve` on the loop's kept schema, so the page shows its run."""
    return [
        sys.executable, "-m", "docmatch", "serve", "--backend", "replay",
        "--run", str(RUN), "--data-dir", str(FIXTURE),
        "--schema", schema, "--port", str(port),
    ]  # fmt: skip


@contextmanager
def review_page(api: int, port: int, out: Path) -> Iterator[None]:
    """The review page built against this run's API and served until the
    block ends. The API's address is read at build time by the page's rewrite,
    so it is built every run."""
    environment = {**os.environ, "DOCMATCH_API_URL": f"http://{HOST}:{api}"}
    if not (REVIEW / "node_modules").is_dir():
        quiet(["npm", "ci"], out / "npm.log", environment)
    quiet(["npm", "run", "build"], out / "build.log", environment)
    next_bin = REVIEW / "node_modules/.bin/next"
    with started(
        [str(next_bin), "start", "--hostname", HOST, "--port", str(port)],
        out / "next.log",
        environment,
        REVIEW,
    ):
        yield


def quiet(command: Sequence[str], log: Path, environment: dict[str, str]) -> None:
    """Run in the review app with its output kept in a log."""
    with log.open("wb") as written:
        code = subprocess.call(
            command,
            cwd=REVIEW,
            stdout=written,
            stderr=subprocess.STDOUT,
            env=environment,
        )
    if code != 0:
        raise DemoError(f"`{' '.join(command)}` failed; see {log}")


@contextmanager
def started(
    command: Sequence[str],
    log: Path,
    environment: dict[str, str] | None = None,
    cwd: Path = ROOT,
) -> Iterator[None]:
    """A server in a process group of its own, stopped with Ctrl-C when the
    block ends and killed if it does not stop. Only that group is signalled."""
    with log.open("wb") as written:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=written,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
        try:
            yield
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        port: int = probe.getsockname()[1]
        return port


def wait_for(url: str, name: str, answers: set[int]) -> None:
    """Return once the URL answers one of the expected codes."""
    deadline = time.monotonic() + STARTUP
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                code = response.status
        except urllib.error.HTTPError as error:
            code = error.code
        except (urllib.error.URLError, ConnectionError):
            code = None
        if code in answers:
            return
        if time.monotonic() > deadline:
            raise DemoError(f"{name} did not answer {url} in {STARTUP:.0f} s")
        time.sleep(0.5)


def browse(out: Path, page: int, video: Path) -> None:
    """Drive one browser session through the beats while it records."""
    session = f"docmatch-demo-{os.getpid()}"
    review = f"http://{HOST}:{page}"
    try:
        browser(session, "open", "about:blank")
        browser(session, "set", "viewport", str(WIDTH), str(HEIGHT))
        browser(session, "record", "start", str(video.resolve()), "--cursor")
        typed(session, out / "loop.html")

        browser(session, "open", f"{review}/?doc={ROUTED}")
        caption(
            session,
            "2  Routed to review with its reason: the invoice bills 2, "
            "the receipt shows 1, a short-ship holds the match",
        )
        browser(session, "wait", "3500")
        browser(session, "click", f'a[href="/?doc={CORRECTED}"]')
        browser(session, "wait", "--text", "Lakeside Tools")
        caption(
            session,
            "3  A correction: the gate failed, total 412.50 against 400.00 due; "
            "the reviewer corrects the amount due and the gate reruns",
        )
        browser(session, "wait", "3500")
        field = f'input[aria-label="{FIELD}"]'
        browser(session, "click", field)
        browser(session, "press", "Control+a")
        browser(session, "keyboard", "type", VALUE)
        browser(session, "press", "Enter")
        browser(session, "wait", "--text", "Gate, totals agree: passed")
        browser(session, "wait", "3000")
        caption(
            session, "4  The reviewer approves; only a human decides a routed document"
        )
        browser(session, "wait", "1500")
        browser(session, "find", "role", "button", "click", "--name", "Approve")
        browser(session, "wait", "--text", "Approved by the reviewer")
        browser(session, "wait", "3000")

        typed(session, out / "pipeline.html")
        browser(session, "record", "stop")
    finally:
        subprocess.run(
            ["agent-browser", "--session", session, "close"], capture_output=True
        )


def browser(session: str, *command: str) -> str:
    done = subprocess.run(
        ["agent-browser", "--session", session, *command],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise DemoError(
            f"agent-browser {' '.join(command)} failed: {done.stderr or done.stdout}"
        )
    return done.stdout


def typed(session: str, terminal_page: Path) -> None:
    """Open a terminal page, let it type its command and print its output,
    and hold the last frame long enough to read."""
    browser(session, "open", terminal_page.resolve().as_uri())
    browser(session, "wait", "--fn", "window.demoDone === true")
    browser(session, "wait", "5000")


def caption(session: str, text: str) -> None:
    """A caption over the page's top right corner, outside its controls."""
    script = """
    (() => {
      let shown = document.getElementById("demo-caption");
      if (!shown) {
        shown = document.createElement("div");
        shown.id = "demo-caption";
        Object.assign(shown.style, {
          position: "fixed", top: "10px", right: "16px", zIndex: "50",
          maxWidth: "560px", padding: "8px 12px", borderRadius: "4px",
          background: "#1d232b", color: "#f4f1ea", pointerEvents: "none",
          font: "500 14px/1.4 system-ui, sans-serif",
        });
        document.body.appendChild(shown);
      }
      shown.textContent = TEXT;
    })()
    """.replace("TEXT", json.dumps(text))
    browser(session, "eval", script)


TERMINAL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>docmatch</title>
<style>
  html, body {{ margin: 0; height: 100%; background: #14171c; }}
  body {{ display: flex; flex-direction: column; color: #d9dde3;
    font: 13px/1.4 ui-monospace, "JetBrains Mono", "DejaVu Sans Mono", monospace; }}
  header {{ padding: 14px 28px; background: #1d232b; color: #f4f1ea;
    font: 500 16px/1.4 system-ui, sans-serif; }}
  pre {{ margin: 0; padding: 20px 28px; white-space: pre; overflow: hidden; }}
  .prompt {{ color: #7fb4ff; }}
  .command {{ color: #f4f1ea; }}
</style></head>
<body><header>{caption}</header><pre id="screen"></pre>
<script>
  const command = {command};
  const output = {output};
  const screen = document.getElementById("screen");
  const prompt = document.createElement("span");
  prompt.className = "prompt";
  prompt.textContent = "$ ";
  const typing = document.createElement("span");
  typing.className = "command";
  screen.append(prompt, typing);
  let at = 0;
  function type() {{
    if (at < command.length) {{
      typing.textContent += command[at++];
      setTimeout(type, 18);
    }} else {{
      screen.append("\\n");
      setTimeout(() => print(0), 500);
    }}
  }}
  function print(line) {{
    if (line < output.length) {{
      screen.append(output[line] + "\\n");
      setTimeout(() => print(line + 1), 70);
    }} else {{
      window.demoDone = true;
    }}
  }}
  setTimeout(type, 600);
</script></body></html>
"""


def terminal(
    path: Path, caption_text: str, command: Sequence[str], output: str
) -> None:
    """A page that types the command as `uv run` would be typed and prints
    the command's real output line by line."""
    shown = "uv run " + shlex.join(command)
    shown = shown.replace(" --", " \\\n    --")
    path.write_text(
        TERMINAL.format(
            caption=html.escape(caption_text),
            command=json.dumps(shown).replace("</", "<\\/"),
            output=json.dumps(output.rstrip("\n").splitlines()).replace("</", "<\\/"),
        )
    )


def drop(database_url: str, schema: str) -> None:
    """The loop's schema and its catalog's, made for this recording alone."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        for name in (schema, f"{schema}_catalog"):
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name))
            )


def encode(raw: Path, video: Path) -> None:
    """H.264 in yuv420p with the index up front, so a browser plays it."""
    done = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(raw),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "23",
            "-movflags",
            "+faststart",
            str(video),
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise DemoError(f"ffmpeg could not encode {raw}: {done.stderr}")
    raw.unlink()


if __name__ == "__main__":
    sys.exit(main())
