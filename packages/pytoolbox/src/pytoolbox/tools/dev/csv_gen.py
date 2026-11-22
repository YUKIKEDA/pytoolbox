"""Synthetic CSV file generation."""

import csv
import errno
import io
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError, PytoolboxError
from pytoolbox.locks import target_lock

COLUMN_TYPES: tuple[str, ...] = ("text", "number", "boolean", "date")
DEFAULT_COLUMNS_TEXT = "name:text\nage:number\nactive:boolean\ncreated:date"

_WORDS: tuple[str, ...] = (
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
)
_BASE_DATE = date(2020, 1, 1)
_DATE_SPAN_DAYS = 2500

_OS_ERROR_REASONS: dict[int, str] = {
    errno.EACCES: "access is denied",
    errno.ENOENT: "path was not found",
    errno.ENOTDIR: "a parent path is not a directory",
    errno.EISDIR: "the path is a directory",
    errno.EROFS: "the file system is read-only",
}


def _os_error_reason(error: OSError) -> str:
    if error.errno is not None and error.errno in _OS_ERROR_REASONS:
        return _OS_ERROR_REASONS[error.errno]
    return error.strerror or str(error)


@dataclass(frozen=True)
class CsvColumn:
    """One column definition for synthetic CSV generation.

    Attributes:
        name: Column header name.
        kind: Value generator type (`text`, `number`, `boolean`, or `date`).
    """

    name: str
    kind: str


@dataclass(frozen=True)
class CsvGenResult:
    """The result of a `csv-gen` run.

    Attributes:
        output: Resolved destination CSV file path.
        rows: Number of data rows generated (or planned on dry-run).
        columns: Column definitions used.
        seed: Random seed used, or `None` when non-deterministic.
        dry_run: Whether writing was skipped.
    """

    output: Path
    rows: int
    columns: tuple[CsvColumn, ...]
    seed: int | None
    dry_run: bool


def parse_columns(specs: Sequence[str]) -> tuple[CsvColumn, ...]:
    """Parse `NAME:TYPE` column specifications.

    Blank entries are ignored so multiline GUI text areas work naturally.

    Args:
        specs: Sequence of `NAME:TYPE` strings.

    Returns:
        Parsed column definitions.

    Raises:
        InputError: No columns are provided, a spec lacks `:`, a name is empty,
            or a column type is not in `COLUMN_TYPES`.
    """
    columns: list[CsvColumn] = []
    for raw in specs:
        entry = raw.strip()
        if not entry:
            continue
        name, sep, kind = entry.partition(":")
        if not sep:
            msg = f"Column {entry!r} must use NAME:TYPE."
            raise InputError(msg)
        col_name = name.strip()
        if not col_name:
            msg = f"Column name in {entry!r} must not be empty."
            raise InputError(msg)
        col_kind = kind.strip().lower()
        if col_kind not in COLUMN_TYPES:
            msg = (
                f"Unknown column type {kind.strip()!r} in {entry!r}. "
                f"Choose from: {', '.join(COLUMN_TYPES)}."
            )
            raise InputError(msg)
        columns.append(CsvColumn(col_name, col_kind))
    if not columns:
        msg = "Specify at least one column as NAME:TYPE."
        raise InputError(msg)
    return tuple(columns)


def _check_no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            msg = f"Links and junctions are not followed: {part}."
            raise InputError(msg)


def _cell_value(kind: str, rng: random.Random) -> str:
    if kind == "text":
        return f"{rng.choice(_WORDS)}_{rng.randint(100, 999)}"
    if kind == "number":
        return str(rng.randint(0, 10_000))
    if kind == "boolean":
        return rng.choice(("true", "false"))
    return (_BASE_DATE + timedelta(days=rng.randint(0, _DATE_SPAN_DAYS))).isoformat()


def _render_csv(rows: int, columns: Sequence[CsvColumn], seed: int | None) -> bytes:
    rng = random.Random(seed)  # noqa: S311 - synthetic test data generator, not cryptographic
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([col.name for col in columns])
    for _ in range(rows):
        writer.writerow([_cell_value(col.kind, rng) for col in columns])
    return buf.getvalue().encode("utf-8-sig")


def generate_csv(
    output: Path,
    rows: int,
    columns: Sequence[CsvColumn],
    *,
    seed: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    state_root: Path | None = None,
) -> CsvGenResult:
    """Write a synthetic UTF-8 BOM CSV file, locking the target and recording history.

    Args:
        output: Destination file path.
        rows: Number of data rows to generate (at least 1).
        columns: Column definitions (at least 1).
        seed: Optional random seed for deterministic output.
        overwrite: Allow replacing an existing output file.
        dry_run: Report the planned output without locking, writing, or recording history.
        state_root: State directory for locks and run history. Defaults to `state_dir()`.

    Returns:
        Summary of the generated or planned CSV file.

    Raises:
        InputError: Invalid row count, empty columns, link/junction path, directory path,
            or existing destination without `overwrite=True`.
        OperationError: Target lock contention or OS write error.
    """
    if rows < 1:
        msg = f"Row count must be at least 1, not {rows}."
        raise InputError(msg)
    parsed_columns = tuple(columns)
    if not parsed_columns:
        msg = "Specify at least one column."
        raise InputError(msg)
    if not str(output).strip():
        msg = "Output path must not be empty."
        raise InputError(msg)

    _check_no_links(output)
    resolved = output.resolve(strict=False)
    if resolved.is_dir():
        msg = f"Output path is a directory: {resolved}."
        raise InputError(msg)
    if resolved.exists() and not overwrite:
        msg = f"Output already exists: {resolved}. Pass overwrite=True to replace it."
        raise InputError(msg)

    if dry_run:
        return CsvGenResult(resolved, rows, parsed_columns, seed, dry_run=True)

    started_at = datetime.now(UTC)
    history = HistoryStore(state_root=state_root)
    history_args: dict[str, object] = {
        "output": str(resolved),
        "rows": rows,
        "columns": [f"{col.name}:{col.kind}" for col in parsed_columns],
        "seed": seed,
        "overwrite": overwrite,
    }
    try:
        with target_lock(resolved, state_root=state_root):
            if resolved.exists() and not overwrite:
                msg = f"Output already exists: {resolved}. Pass overwrite=True to replace it."
                raise InputError(msg)
            payload = _render_csv(rows, parsed_columns, seed)
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_bytes(payload)
            except OSError as error:
                msg = f"Cannot write {resolved}: {_os_error_reason(error)}."
                raise OperationError(msg) from error
    except PytoolboxError as error:
        history.record(
            "csv-gen",
            history_args,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            succeeded=0,
            failed=1,
            error=str(error),
        )
        raise

    history.record(
        "csv-gen",
        history_args,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        succeeded=1,
    )
    return CsvGenResult(resolved, rows, parsed_columns, seed, dry_run=False)
