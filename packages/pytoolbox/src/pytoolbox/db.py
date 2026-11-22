"""SQLite run history for tools that write or delete."""

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pytoolbox.errors import InputError, OperationError
from pytoolbox.state import state_dir

HISTORY_FILENAME = "history.sqlite3"
DEFAULT_HISTORY_LIMIT = 200
DEFAULT_RECENT_TOOLS = 6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,
    arguments TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    succeeded INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    cancelled INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started_id ON runs (started_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_runs_tool_started_id ON runs (tool, started_at DESC, id DESC);
"""


@dataclass(frozen=True)
class RunRecord:
    """One recorded execution of a tool that writes or deletes.

    Attributes:
        id: SQLite row ID.
        tool: Tool CLI name.
        arguments: Tool arguments stored as JSON-compatible data.
        started_at: Time when the run started, timezone-aware UTC.
        finished_at: Time when the run finished, timezone-aware UTC.
        succeeded: Number of items that succeeded.
        failed: Number of items that failed.
        cancelled: Whether the run was cancelled before completion.
        error: Error message if the run raised, or `None`.
    """

    id: int
    tool: str
    arguments: dict[str, object]
    started_at: datetime
    finished_at: datetime
    succeeded: int
    failed: int
    cancelled: bool
    error: str | None = None

    @property
    def status(self) -> str:
        """Return one of `cancelled`, `failed`, `partial`, or `succeeded`."""
        if self.cancelled:
            return "cancelled"
        if self.error is not None or (self.succeeded == 0 and self.failed > 0):
            return "failed"
        if self.failed > 0:
            return "partial"
        return "succeeded"


def history_path(*, state_root: Path | None = None) -> Path:
    """Return `<state>/history.sqlite3`.

    Args:
        state_root: State directory root. Defaults to `state_dir()`.

    Returns:
        Path to the SQLite run history database.
    """
    root = state_dir() if state_root is None else state_root
    return root / HISTORY_FILENAME


def _to_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        msg = f"{name} must be timezone-aware."
        raise InputError(msg)
    return value.astimezone(UTC)


def _parse_arguments(raw_args: str, context: str) -> dict[str, object]:
    parsed: object = json.loads(raw_args)
    if not isinstance(parsed, dict):
        msg = f"Invalid arguments JSON in {context}."
        raise OperationError(msg)
    typed = cast("dict[object, object]", parsed)
    return {str(key): val for key, val in typed.items()}


def _decode_row(row: tuple[int, str, str, str, str, int, int, int, str | None]) -> RunRecord:
    row_id, tool, raw_args, raw_started, raw_finished, succeeded, failed, cancelled, error = row
    return RunRecord(
        id=row_id,
        tool=tool,
        arguments=_parse_arguments(raw_args, f"history row {row_id}"),
        started_at=datetime.fromisoformat(raw_started).astimezone(UTC),
        finished_at=datetime.fromisoformat(raw_finished).astimezone(UTC),
        succeeded=succeeded,
        failed=failed,
        cancelled=bool(cancelled),
        error=error,
    )


class HistoryStore:
    """SQLite store for tool run history under `<state>/history.sqlite3`."""

    def __init__(self, path: Path | None = None, *, state_root: Path | None = None) -> None:
        self.path = history_path(state_root=state_root) if path is None else path

    def record(
        self,
        tool: str,
        arguments: Mapping[str, object],
        *,
        started_at: datetime,
        finished_at: datetime,
        succeeded: int = 0,
        failed: int = 0,
        cancelled: bool = False,
        error: str | None = None,
    ) -> RunRecord:
        """Record one completed or failed tool run.

        Args:
            tool: Tool CLI name.
            arguments: Tool arguments to serialize as JSON.
            started_at: Run start timestamp (timezone-aware).
            finished_at: Run finish timestamp (timezone-aware).
            succeeded: Count of succeeded items.
            failed: Count of failed items.
            cancelled: Whether the run was cancelled.
            error: Failure message if an error stopped the run.

        Returns:
            The stored `RunRecord`.

        Raises:
            InputError: The tool name is empty, counts are negative, or timestamps are naive.
            OperationError: The SQLite database could not be created or written.
        """
        if not tool.strip():
            msg = "Tool name must not be empty."
            raise InputError(msg)
        if succeeded < 0 or failed < 0:
            msg = "Counts must be non-negative."
            raise InputError(msg)
        utc_start = _to_utc(started_at, "started_at")
        utc_finish = _to_utc(finished_at, "finished_at")
        args_json = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, default=str)

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as conn, conn:
                conn.executescript(_SCHEMA)
                cursor = conn.execute(
                    """
                    INSERT INTO runs (
                        tool, arguments, started_at, finished_at,
                        succeeded, failed, cancelled, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tool,
                        args_json,
                        utc_start.isoformat(),
                        utc_finish.isoformat(),
                        succeeded,
                        failed,
                        int(cancelled),
                        error,
                    ),
                )
                row_id = cursor.lastrowid
                if row_id is None:
                    msg = "Failed to insert run history row."
                    raise OperationError(msg)
        except (OSError, sqlite3.Error) as exc:
            msg = f"Cannot write run history at {self.path}: {exc}."
            raise OperationError(msg) from exc

        return RunRecord(
            id=row_id,
            tool=tool,
            arguments=_parse_arguments(args_json, "recorded arguments"),
            started_at=utc_start,
            finished_at=utc_finish,
            succeeded=succeeded,
            failed=failed,
            cancelled=cancelled,
            error=error,
        )

    def list_runs(
        self,
        *,
        tool: str | None = None,
        limit: int | None = None,
    ) -> tuple[RunRecord, ...]:
        """Return recorded runs newest first, optionally filtered by tool and capped for display.

        Args:
            tool: When given and non-empty, return only runs for that tool.
            limit: Maximum number of rows to return, or `None` for all rows.

        Returns:
            Matching `RunRecord` entries ordered newest first.

        Raises:
            InputError: `limit` is negative.
            OperationError: The existing database could not be read.
        """
        if limit is not None and limit < 0:
            msg = "Limit must be non-negative."
            raise InputError(msg)
        if limit == 0 or not self.path.is_file():
            return ()

        query = (
            "SELECT id, tool, arguments, started_at, finished_at, "
            "succeeded, failed, cancelled, error FROM runs"
        )
        params: list[object] = []
        if tool:
            query += " WHERE tool = ?"
            params.append(tool)
        query += " ORDER BY started_at DESC, id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        try:
            with closing(sqlite3.connect(self.path)) as conn:
                rows = conn.execute(query, params).fetchall()
        except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError) as exc:
            msg = f"Cannot read run history at {self.path}: {exc}."
            raise OperationError(msg) from exc

        return tuple(_decode_row(row) for row in rows)

    def recent_tools(self, *, limit: int | None = DEFAULT_RECENT_TOOLS) -> tuple[str, ...]:
        """Return distinct tool names ordered by their latest run.

        Args:
            limit: Maximum number of tool names to return, or `None` for all.

        Returns:
            Distinct tool names, most recently run first.

        Raises:
            InputError: `limit` is negative.
            OperationError: The existing database could not be read.
        """
        if limit is not None and limit < 0:
            msg = "Limit must be non-negative."
            raise InputError(msg)
        if limit == 0 or not self.path.is_file():
            return ()

        query = "SELECT tool FROM runs GROUP BY tool ORDER BY MAX(started_at) DESC, MAX(id) DESC"
        params: list[object] = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        try:
            with closing(sqlite3.connect(self.path)) as conn:
                rows = conn.execute(query, params).fetchall()
        except (OSError, sqlite3.Error) as exc:
            msg = f"Cannot read run history at {self.path}: {exc}."
            raise OperationError(msg) from exc

        return tuple(str(row[0]) for row in rows)

    def list_tools(self) -> tuple[str, ...]:
        """Return distinct tool names present in the history database, sorted by name."""
        if not self.path.is_file():
            return ()
        try:
            with closing(sqlite3.connect(self.path)) as conn:
                rows = conn.execute("SELECT DISTINCT tool FROM runs ORDER BY tool ASC").fetchall()
        except (OSError, sqlite3.Error) as exc:
            msg = f"Cannot read run history at {self.path}: {exc}."
            raise OperationError(msg) from exc

        return tuple(str(row[0]) for row in rows)
