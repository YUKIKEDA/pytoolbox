"""Batch file copying with numbered destination names, locking, and run history."""

import errno
import shutil
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError, PytoolboxError
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, Cancel, ItemFailure, Progress

DEFAULT_FORMAT = "{stem}_{n:03d}{suffix}"
"""Default output filename template for `file-copy`."""

_WIN_SHARING_VIOLATION = 32
_WIN_LOCK_VIOLATION = 33

_OS_ERROR_REASONS: dict[int, str] = {
    errno.EACCES: "access is denied",
    errno.EPERM: "permission is denied",
    errno.EBUSY: "file is busy",
    errno.ENOENT: "path was not found",
    errno.ENOTDIR: "a parent path is not a directory",
    errno.EISDIR: "the path is a directory",
    errno.EEXIST: "the path already exists",
    errno.EROFS: "the file system is read-only",
}


@dataclass(frozen=True)
class FileCopyResult:
    """Outcome of a file-copy batch, yielded last.

    Attributes:
        source: Resolved source file path.
        dest_dir: Resolved destination directory path.
        items: Destination paths planned (when `dry_run`) or written.
        dry_run: Whether copying was skipped.
        batch: Summary counts, per-item failures, and cancellation flag.
    """

    source: Path
    dest_dir: Path
    items: tuple[Path, ...]
    dry_run: bool
    batch: BatchResult


def _os_error_reason(error: OSError) -> str:
    """Return a fixed English message for common OS file errors."""
    winerror: object = getattr(error, "winerror", None)
    if isinstance(winerror, int) and winerror in {_WIN_SHARING_VIOLATION, _WIN_LOCK_VIOLATION}:
        return "file is in use by another process"
    if error.errno is not None and error.errno in _OS_ERROR_REASONS:
        return _OS_ERROR_REASONS[error.errno]
    return error.strerror or str(error)


def _is_link_path(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _check_no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if _is_link_path(part):
            msg = f"Links and junctions are not followed: {part}."
            raise InputError(msg)


def _resolve_relative_target(dest_resolved: Path, rel_name: str) -> Path:
    rel_path = Path(rel_name)
    if rel_path.is_absolute() or rel_path.drive or rel_path.root:
        msg = f"Destination name must stay inside destination folder: {rel_name!r}."
        raise InputError(msg)
    parts = rel_path.parts
    if not parts or parts[-1] in (".", ".."):
        msg = f"Destination path escapes destination folder: {rel_name!r}."
        raise InputError(msg)

    cur = dest_resolved
    for index, part in enumerate(parts):
        if part == "..":
            if cur == dest_resolved:
                msg = f"Destination path escapes destination folder: {rel_name!r}."
                raise InputError(msg)
            cur = cur.parent
            continue
        cur = cur / part
        if index < len(parts) - 1 and _is_link_path(cur):
            msg = f"Links and junctions are not followed: {cur}."
            raise InputError(msg)
    return cur


def _expand_targets(
    source: Path,
    source_resolved: Path,
    dest_resolved: Path,
    count: int,
    *,
    fmt: str,
    start: int,
    overwrite: bool,
) -> tuple[Path, ...]:
    targets: list[Path] = []
    seen: set[Path] = set()
    stem = source.stem
    suffix = source.suffix
    name = source.name

    for n in range(start, start + count):
        try:
            rel_name = fmt.format(stem=stem, suffix=suffix, name=name, n=n)
        except (ValueError, KeyError, IndexError, AttributeError) as error:
            msg = f"Invalid format {fmt!r}: {error}."
            raise InputError(msg) from error

        if not rel_name or not rel_name.strip():
            msg = "Generated destination name must not be empty."
            raise InputError(msg)
        if rel_name.endswith(("/", "\\", "/.", "\\.")):
            msg = f"Destination name must name a file, not a folder: {rel_name!r}."
            raise InputError(msg)

        target = _resolve_relative_target(dest_resolved, rel_name)
        if target == source_resolved:
            msg = f"Destination path cannot overwrite source file: {target}."
            raise InputError(msg)
        if target in seen:
            msg = f"Duplicate destination path from format {fmt!r}: {target}."
            raise InputError(msg)
        if not overwrite and (target.exists() or _is_link_path(target)):
            msg = f"Destination already exists: {target}. Pass overwrite=True to replace it."
            raise InputError(msg)

        seen.add(target)
        targets.append(target)

    return tuple(targets)


def copy_file_batch(
    source: Path,
    dest_dir: Path,
    count: int,
    *,
    fmt: str = DEFAULT_FORMAT,
    start: int = 1,
    overwrite: bool = False,
    dry_run: bool = False,
    state_root: Path | None = None,
    cancel: Cancel | None = None,
) -> Iterator[Progress | FileCopyResult]:
    """Copy one file to numbered destination paths inside a destination directory.

    Inputs, paths, format expansion, collisions, and existing targets are validated
    synchronously before returning the stream iterator. Non-`dry_run` runs create
    `dest_dir`, lock every expanded destination path via `target_lock`, copy with
    `shutil.copy2`, and record the run in `HistoryStore`.

    Args:
        source: Source regular file path. Links and junctions are not followed.
        dest_dir: Destination directory path. Created if missing when not `dry_run`.
        count: Number of copies to create (at least 1).
        fmt: Filename template using `{stem}`, `{suffix}`, `{name}`, and `{n}`.
        start: First index value for `{n}`.
        overwrite: Allow replacing existing regular destination files.
        dry_run: Plan target paths without creating directories, locking, copying, or
            recording run history.
        state_root: State directory for locks and run history. Defaults to `state_dir()`.
        cancel: Optional cancellation callback checked before each copy.

    Returns:
        An iterator yielding `Progress` per target and one `FileCopyResult` last.

    Raises:
        InputError: Invalid count/format, link or junction path, missing or non-file
            `source`, non-directory `dest_dir`, path traversal, duplicate target,
            self-overwrite, or existing target when `overwrite=False`.
        OperationError: `dest_dir` cannot be created or a target lock is held.
    """
    if count < 1:
        msg = f"Count must be at least 1, not {count}."
        raise InputError(msg)
    if not fmt.strip():
        msg = "Format must not be empty."
        raise InputError(msg)
    if not str(source).strip():
        msg = "Source path must not be empty."
        raise InputError(msg)
    if not str(dest_dir).strip():
        msg = "Destination folder must not be empty."
        raise InputError(msg)

    _check_no_links(source)
    _check_no_links(dest_dir)

    if not source.exists():
        msg = f"Source file was not found: {source}."
        raise InputError(msg)
    if not source.is_file():
        msg = f"Source path is not a regular file: {source}."
        raise InputError(msg)
    if dest_dir.exists() and not dest_dir.is_dir():
        msg = f"Destination path is not a directory: {dest_dir}."
        raise InputError(msg)

    source_resolved = source.resolve(strict=True)
    dest_resolved = dest_dir.resolve(strict=False)
    targets = _expand_targets(
        source,
        source_resolved,
        dest_resolved,
        count,
        fmt=fmt,
        start=start,
        overwrite=overwrite,
    )
    return _copy_batch(
        source_resolved,
        dest_resolved,
        targets,
        count=count,
        fmt=fmt,
        start=start,
        overwrite=overwrite,
        dry_run=dry_run,
        state_root=state_root,
        cancel=(lambda: False) if cancel is None else cancel,
    )


def _copy_batch(
    source_resolved: Path,
    dest_resolved: Path,
    targets: tuple[Path, ...],
    *,
    count: int,
    fmt: str,
    start: int,
    overwrite: bool,
    dry_run: bool,
    state_root: Path | None,
    cancel: Cancel,
) -> Iterator[Progress | FileCopyResult]:
    started_at = datetime.now(UTC)
    history = None if dry_run else HistoryStore(state_root=state_root)
    arguments: dict[str, object] = {
        "source": str(source_resolved),
        "dest_dir": str(dest_resolved),
        "count": count,
        "format": fmt,
        "start": start,
        "overwrite": overwrite,
    }
    succeeded_items: list[Path] = []
    failures: list[ItemFailure] = []
    cancelled = False
    total = len(targets)

    try:
        if not dry_run:
            try:
                dest_resolved.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                msg = (
                    f"Cannot create destination folder {dest_resolved}: {_os_error_reason(error)}."
                )
                raise OperationError(msg) from error

        lock_ctx = nullcontext() if dry_run else target_lock(targets, state_root=state_root)
        with lock_ctx:
            for completed, target in enumerate(targets, start=1):
                if cancel():
                    cancelled = True
                    break
                if dry_run:
                    succeeded_items.append(target)
                else:
                    try:
                        if _is_link_path(target):
                            failures.append(
                                ItemFailure(target, "Links and junctions are not followed.")
                            )
                        elif target.is_dir():
                            failures.append(ItemFailure(target, "the path is a directory"))
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source_resolved, target)
                            succeeded_items.append(target)
                    except OSError as error:
                        failures.append(ItemFailure(target, _os_error_reason(error)))
                yield Progress(completed, total, target)
    except PytoolboxError as error:
        if history is not None:
            history.record(
                "file-copy",
                arguments,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                succeeded=len(succeeded_items),
                failed=len(failures),
                cancelled=cancelled,
                error=str(error),
            )
        raise
    except (KeyboardInterrupt, GeneratorExit):
        if history is not None:
            history.record(
                "file-copy",
                arguments,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                succeeded=len(succeeded_items),
                failed=len(failures),
                cancelled=True,
            )
        raise

    result = FileCopyResult(
        source=source_resolved,
        dest_dir=dest_resolved,
        items=tuple(succeeded_items),
        dry_run=dry_run,
        batch=BatchResult(
            succeeded=len(succeeded_items),
            failures=tuple(failures),
            cancelled=cancelled,
        ),
    )
    if history is not None:
        history.record(
            "file-copy",
            arguments,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            succeeded=result.batch.succeeded,
            failed=len(result.batch.failures),
            cancelled=result.batch.cancelled,
        )
    yield result
