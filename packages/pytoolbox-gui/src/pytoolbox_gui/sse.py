"""Streaming runs and their server-sent events."""

import logging
import queue
import threading
from collections.abc import Callable, Iterator
from uuid import uuid4

from pytoolbox.errors import OperationError, PytoolboxError
from pytoolbox.results import BatchResult, Cancel, LogEvent, Progress

type Value = Progress | LogEvent | BatchResult
type Stream = Callable[[Cancel], Iterator[Value]]
"""Starts the tool with a cancel check and yields what it reports."""

type Event = Value | PytoolboxError

_logger = logging.getLogger(__name__)


def _checked(value: object) -> Value:
    # Tool code is not bound by the type checker at run time; event_name needs a known type.
    if isinstance(value, Progress | LogEvent | BatchResult):
        return value
    msg = f"A stream yielded {type(value).__name__}, not Progress, LogEvent, or BatchResult."
    raise TypeError(msg)


class Run:
    """One streaming run of a tool, worked in a thread.

    The run has one reader: the SSE response that takes its events.
    """

    def __init__(self, stream: Stream) -> None:
        self.id = uuid4().hex
        self._stream = stream
        self._cancel = threading.Event()
        self._events: queue.Queue[Event | None] = queue.Queue()
        self._thread = threading.Thread(target=self._work, daemon=True)

    def start(self) -> None:
        """Start the tool in its thread."""
        self._thread.start()

    def cancel(self) -> None:
        """Ask the tool to stop at its next check."""
        self._cancel.set()

    def cancelled(self) -> bool:
        """Return whether cancel was asked. This is the tool's `cancel` callable."""
        return self._cancel.is_set()

    def next_event(self) -> Event | None:
        """Block until the next event. `None` means the run has ended."""
        return self._events.get()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the thread to end."""
        self._thread.join(timeout)

    def _work(self) -> None:
        try:
            for value in self._stream(self.cancelled):
                self._events.put(_checked(value))
        except PytoolboxError as error:
            self._events.put(error)
        except Exception:
            _logger.exception("Run %s failed", self.id)
            self._events.put(OperationError("The run failed unexpectedly. See the server log."))
        finally:
            self._events.put(None)


class Runs:
    """The runs of this server process, held in memory."""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def start(self, stream: Stream) -> Run:
        """Start a run and keep it under its ID."""
        run = Run(stream)
        with self._lock:
            self._runs[run.id] = run
        run.start()
        return run

    def get(self, run_id: str) -> Run | None:
        """Return the run with that ID, or `None`."""
        with self._lock:
            return self._runs.get(run_id)

    def cancel_all(self) -> None:
        """Cancel every run. The server calls this when it stops."""
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            run.cancel()


def event_name(event: Event) -> str:
    """Return the SSE event name for an event, as `docs/conventions.md` names them."""
    match event:
        case Progress():
            return "progress"
        case LogEvent():
            return "log"
        case BatchResult():
            return "result"
        case PytoolboxError():
            return "error"


def format_event(name: str, data: str) -> str:
    """Encode one server-sent event.

    Each line of `data` becomes its own `data:` field, so a multi-line HTML fragment stays whole.

    Args:
        name: The event name.
        data: The payload. An empty payload is sent as one empty field.

    Returns:
        The event text, ending with the blank line that dispatches it.
    """
    lines = data.splitlines() or [""]
    fields = "".join(f"data: {line}\n" for line in lines)
    return f"event: {name}\n{fields}\n"
