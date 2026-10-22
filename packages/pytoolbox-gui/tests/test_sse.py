from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from pytoolbox.errors import InputError, OperationError
from pytoolbox.results import BatchResult, Cancel, LogEvent, Progress
from pytoolbox_gui.sse import Event, Run, Runs, Stream, Value, event_name, format_event


def test_format_event_splits_lines_into_data_fields() -> None:
    assert (
        format_event("log", "<p>a</p>\n<p>b</p>")
        == "event: log\ndata: <p>a</p>\ndata: <p>b</p>\n\n"
    )


def test_format_event_sends_one_empty_field_for_empty_data() -> None:
    assert format_event("done", "") == "event: done\ndata: \n\n"


@pytest.mark.parametrize(
    ("event", "name"),
    [
        (Progress(1, 2, Path("a")), "progress"),
        (LogEvent(datetime(2026, 1, 1, tzinfo=UTC), "x"), "log"),
        (BatchResult(1), "result"),
        (InputError("x"), "error"),
    ],
)
def test_event_name(event: Event, name: str) -> None:
    assert event_name(event) == name


def _drain(run: Run) -> list[Event]:
    events: list[Event] = []
    while (event := run.next_event()) is not None:
        events.append(event)
    run.join(1)
    return events


def test_run_passes_values_then_ends() -> None:
    def stream(_cancel: Cancel) -> Iterator[Value]:
        yield BatchResult(2)

    run = Run(stream)
    run.start()

    assert _drain(run) == [BatchResult(2)]


def test_run_reports_a_tool_error() -> None:
    def stream(_cancel: Cancel) -> Iterator[Value]:
        msg = "locked"
        raise OperationError(msg)
        yield BatchResult(0)

    run = Run(stream)
    run.start()
    events = _drain(run)

    assert len(events) == 1
    assert isinstance(events[0], OperationError)
    assert str(events[0]) == "locked"


def test_run_turns_an_unexpected_exception_into_an_operation_error() -> None:
    def stream(_cancel: Cancel) -> Iterator[Value]:
        msg = "bug"
        raise RuntimeError(msg)
        yield BatchResult(0)

    run = Run(stream)
    run.start()
    events = _drain(run)

    assert isinstance(events[0], OperationError)
    assert "unexpectedly" in str(events[0])


def test_run_stops_with_an_operation_error_when_a_tool_yields_an_unknown_value() -> None:
    def stream(_cancel: Cancel) -> Iterator[object]:
        yield BatchResult(1)
        yield "not a result"
        yield BatchResult(2)

    run = Run(cast("Stream", stream))
    run.start()
    events = _drain(run)

    assert events[0] == BatchResult(1)
    assert len(events) == 2
    assert isinstance(events[1], OperationError)
    assert event_name(events[1]) == "error"


def test_cancel_reaches_the_tool() -> None:
    def stream(cancel: Cancel) -> Iterator[Value]:
        yield BatchResult(0, cancelled=cancel())

    run = Run(stream)
    run.cancel()
    run.start()

    assert _drain(run) == [BatchResult(0, cancelled=True)]


def test_runs_keep_runs_by_id() -> None:
    def stream(_cancel: Cancel) -> Iterator[Value]:
        yield BatchResult(0)

    runs = Runs()
    run = runs.start(stream)

    assert runs.get(run.id) is run
    assert runs.get("nope") is None
    runs.cancel_all()
    assert run.cancelled()
    _drain(run)
