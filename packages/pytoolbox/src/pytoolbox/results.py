"""Values that long-running tools yield and return."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

type Cancel = Callable[[], bool]
"""Returns True when the caller wants the call to stop between items."""


@dataclass(frozen=True)
class Progress:
    """How far a batch has got.

    Attributes:
        completed: Items finished so far, successful or not.
        total: Items in the batch.
        path: The item just finished.
    """

    completed: int
    total: int
    path: Path


@dataclass(frozen=True)
class ItemFailure:
    """One item of a batch that failed while the rest continued.

    Attributes:
        path: The item that failed.
        message: Why it failed, in English.
    """

    path: Path
    message: str


@dataclass(frozen=True)
class BatchResult:
    """The outcome of a batch, yielded last.

    Attributes:
        succeeded: How many items succeeded.
        failures: The items that failed.
        cancelled: Whether the caller stopped the batch before the end.
    """

    succeeded: int
    failures: tuple[ItemFailure, ...] = field(default=())
    cancelled: bool = False


@dataclass(frozen=True)
class LogEvent:
    """One line of a session's log.

    Attributes:
        time: When it happened, timezone-aware UTC.
        message: The line, in English.
    """

    time: datetime
    message: str
