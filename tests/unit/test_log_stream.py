"""Unit tests for the SSE log tailer's byte/line handling.

`_consume_log_lines` is the pure core of the `/jobs/{id}/logs/stream` tailer: it
turns a raw byte buffer read from the log file into whole log lines without
crashing on a UTF-8 sequence split across a read boundary, and without emitting a
line that has not been fully written yet.
"""

from snektest import assert_eq, test

from danube.api.routes.control import _consume_log_lines


@test(mark="medium")
def test_complete_lines_are_all_consumed() -> None:
    lines, consumed = _consume_log_lines(b"one\ntwo\n", flush=False)

    assert_eq(lines, ["one", "two"])
    assert_eq(consumed, 8)


@test(mark="medium")
def test_partial_trailing_line_is_held_until_newline() -> None:
    # "two" has no trailing newline yet: it must not be emitted, and only the
    # bytes up to (and including) the last newline are consumed.
    lines, consumed = _consume_log_lines(b"one\ntwo", flush=False)

    assert_eq(lines, ["one"])
    assert_eq(consumed, 4)


@test(mark="medium")
def test_flush_emits_trailing_line_without_newline() -> None:
    # On the terminal pass the writer has closed the file, so a final line with no
    # trailing newline is emitted rather than dropped.
    lines, consumed = _consume_log_lines(b"one\ntwo", flush=True)

    assert_eq(lines, ["one", "two"])
    assert_eq(consumed, 7)


@test(mark="medium")
def test_multibyte_split_across_boundary_does_not_crash() -> None:
    # "café\n" is b"caf\xc3\xa9\n"; a read that stops mid-character (after \xc3)
    # must not raise UnicodeDecodeError and must consume nothing.
    lines, consumed = _consume_log_lines(b"caf\xc3", flush=False)

    assert_eq(lines, [])
    assert_eq(consumed, 0)


@test(mark="medium")
def test_complete_multibyte_line_decodes() -> None:
    lines, consumed = _consume_log_lines("café\n".encode(), flush=False)

    assert_eq(lines, ["café"])
    assert_eq(consumed, 6)


@test(mark="medium")
def test_empty_buffer_consumes_nothing() -> None:
    assert_eq(_consume_log_lines(b"", flush=False), ([], 0))
    assert_eq(_consume_log_lines(b"", flush=True), ([], 0))
