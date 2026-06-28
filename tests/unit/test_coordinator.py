"""Tests for the Coordinator entrypoint's danubefile loading.

`load_pipeline` is the pure, Podman-free part of the Coordinator: it imports a
`danubefile.py` from a path and hands back its `pipeline` coroutine. The full
run-the-pipeline path (including failure reporting) is covered end-to-end in the
in-process job-run integration test.
"""

import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

from snektest import assert_raises, fixture, load_fixture, test

from danube.coordinator import DanubefileError, load_pipeline

_VALID_DANUBEFILE = """
async def pipeline(danube):
    return None
"""


@fixture
def workdir() -> Generator[Path]:
    path = Path(tempfile.mkdtemp(prefix="danube-coord-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@test(mark="fast")
def test_load_pipeline_returns_callable() -> None:
    work = load_fixture(workdir())
    source = work / "danubefile.py"
    _ = source.write_text(_VALID_DANUBEFILE)

    pipeline = load_pipeline(source)

    assert callable(pipeline)


@test(mark="fast")
def test_load_pipeline_missing_file_raises() -> None:
    work = load_fixture(workdir())

    with assert_raises(DanubefileError):
        _ = load_pipeline(work / "nope.py")


@test(mark="fast")
def test_load_pipeline_without_pipeline_symbol_raises() -> None:
    work = load_fixture(workdir())
    source = work / "danubefile.py"
    _ = source.write_text("x = 1\n")

    with assert_raises(DanubefileError):
        _ = load_pipeline(source)
