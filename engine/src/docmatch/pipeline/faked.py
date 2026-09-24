"""`docmatch` with the embedder faked, run as `python -m docmatch.pipeline.faked`.

The loop's CLI test starts `serve` as a subprocess, where no monkeypatch
reaches, so it points `measure.COMMAND` here: the same command line, with the
resolution tests' fake in place of `load_models`, so no test loads model
weights (#167). CI's Pipeline step runs the real server.
"""

import sys

import pytest

from docmatch import cli
from docmatch.resolution.conftest import fake_loader

if __name__ == "__main__":
    pytest.MonkeyPatch().setattr(cli, "load_models", fake_loader)
    sys.exit(cli.main(sys.argv[1:]))
