"""Append-only per-job log writer.

The Master streams each step's captured output to ``<data_dir>/logs/<job_id>.log``
(see `docs/architecture/components.md`, Log Writer). `LogWriter` owns one such
file: it opens it append-binary, tracks the current byte offset, and returns the
``[start, end)`` offset pair for every write so the orchestrator can record
`steps.log_offset_start` / `steps.log_offset_end`.

File IO goes through `anyio` to match the async runtime used across the codebase.
The writer is an async context manager; the file handle lives only between
``__aenter__`` and ``__aexit__``.
"""

from pathlib import Path
from types import TracebackType
from typing import Self

import anyio


class LogWriterError(Exception):
    """Raised when a `LogWriter` is used outside its open async context."""


class LogWriter:
    """Async, append-only writer for a single job's log file.

    Offsets are byte offsets into the file: a write of ``n`` UTF-8 bytes returns
    ``(start, start + n)`` and advances the tracked offset by ``n``. Opening an
    existing file resumes from its current size, keeping the log append-only.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._offset = 0
        self._file: anyio.AsyncFile[bytes] | None = None

    async def __aenter__(self) -> Self:
        await anyio.Path(self._path.parent).mkdir(parents=True, exist_ok=True)
        target = anyio.Path(self._path)
        if await target.exists():
            stat = await target.stat()
            self._offset = stat.st_size
        self._file = await anyio.open_file(self._path, "ab")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._file is not None:
            await self._file.aclose()
            self._file = None

    async def write(self, text: str) -> tuple[int, int]:
        """Append ``text`` and return its ``(start, end)`` byte offsets."""
        if self._file is None:
            message = "LogWriter.write called outside its async context"
            raise LogWriterError(message)
        data = text.encode("utf-8")
        start = self._offset
        _ = await self._file.write(data)
        await self._file.flush()
        self._offset += len(data)
        return start, self._offset
