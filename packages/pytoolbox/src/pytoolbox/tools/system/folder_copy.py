"""Batch folder copying with numbered names, target locking, and run history."""

import errno
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError, PytoolboxError
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, Cancel, ItemFailure, Progress

DEFAULT_FORMAT = "{name}_{n:03d}"
"""Default folder name template for copied folders."""

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
    errno.ENOSPC: "no space left on device",
}


@dataclass(frozen=True)
class FolderCopyResult:
    """Outcome of a folder-copy run, yielded last.

    Attributes:
        source: Resolved source directory path.
        dest_dir: Resolved destination directory path.
        items: Target folder paths planned (when `dry_run`) or copied.
        dry_run: Whether copying was skipped.
        batch: Summary counts, per-item failures, and cancellation flag.
    """

    source: Path
    dest_dir: Path
    items: tuple[Path, ...]
    dry_run: bool
    batch: BatchResult


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _os_error_reason(error: OSError) -> str:
    """Return a fixed English message for common OS file errors."""
    winerror: object = getattr(error, "winerror", None)
    if isinstance(winerror, int) and winerror in {_WIN_SHARING_VIOLATION, _WIN_LOCK_VIOLATION}:
        return "file is in use by another process"
    if error.errno is not None and error.errno in _OS_ERROR_REASONS:
        return _OS_ERROR_REASONS[error.errno]
    return error.strerror or str(error)


def _is_link_entry(entry: os.DirEntry[str]) -> bool:
    return entry.is_symlink() or entry.is_junction()


def _is_link_path(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _check_no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if _is_link_path(part):
            msg = f"Links and junctions are not followed: {part}."
            raise InputError(msg)


def _format_target_name(fmt: str, name: str, n: int) -> str:
    if not fmt.strip():
        msg = "Folder name format must not be empty."
        raise InputError(msg)
    try:
        return fmt.format(name=name, n=n)
    except (ValueError, KeyError, IndexError) as error:
        msg = f"Invalid folder name format {fmt!r}: {error}."
        raise InputError(msg) from error


def _expand_targets(
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
    folder_name = source_resolved.name
    for offset in range(count):
        n = start + offset
        relative_text = _format_target_name(fmt, folder_name, n)
        if not relative_text.strip():
            msg = f"Folder name format {fmt!r} produced an empty target name for n={n}."
            raise InputError(msg)
        rel_path = Path(relative_text)
        if rel_path.is_absolute() or rel_path.drive or rel_path.root or ".." in rel_path.parts:
            msg = f"Target name {relative_text!r} must be a relative path inside {dest_resolved}."
            raise InputError(msg)
        candidate = dest_resolved / rel_path
        _check_no_links(candidate)
        target_resolved = candidate.resolve(strict=False)
        if target_resolved == dest_resolved or not target_resolved.is_relative_to(dest_resolved):
            msg = f"Target path {target_resolved} escapes destination directory {dest_resolved}."
            raise InputError(msg)
        if target_resolved == source_resolved or source_resolved.is_relative_to(target_resolved):
            msg = f"Target path cannot match or contain source folder: {target_resolved}."
            raise InputError(msg)
        if target_resolved in seen:
            msg = f"Duplicate target folder path: {target_resolved}."
            raise InputError(msg)
        if not overwrite and target_resolved.exists():
            msg = f"Target already exists: {target_resolved}. Pass overwrite=True to replace it."
            raise InputError(msg)
        seen.add(target_resolved)
        targets.append(target_resolved)
    return tuple(targets)


def _remove_link(path: Path) -> None:
    if path.is_junction():
        path.rmdir()
        return
    try:
        path.unlink()
    except OSError:
        path.rmdir()


def _remove_existing_target(path: Path) -> None:
    if _is_link_path(path):
        _remove_link(path)
        return
    if not path.exists():
        return
    if not path.is_dir():
        path.unlink()
        return
    dirs_to_rmdir: list[Path] = []
    pending: list[Path] = [path]
    while pending:
        folder = pending.pop()
        dirs_to_rmdir.append(folder)
        with os.scandir(folder) as scan:
            for entry in scan:
                entry_path = Path(entry.path)
                if _is_link_entry(entry):
                    _remove_link(entry_path)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(entry_path)
                else:
                    entry_path.unlink()
    for folder in reversed(dirs_to_rmdir):
        folder.rmdir()


def _copy_tree_no_follow(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[Path, Path]] = [(source, target)]
    while pending:
        src_dir, dst_dir = pending.pop()
        with os.scandir(src_dir) as scan:
            entries = sorted(scan, key=lambda entry: entry.name.casefold())
        for entry in entries:
            if _is_link_entry(entry):
                continue
            src_entry = Path(entry.path)
            dst_entry = dst_dir / entry.name
            if entry.is_dir(follow_symlinks=False):
                dst_entry.mkdir(exist_ok=True)
                pending.append((src_entry, dst_entry))
            elif entry.is_file(follow_symlinks=False):
                shutil.copy2(src_entry, dst_entry, follow_symlinks=False)


def _copy_one_target(source: Path, target: Path, *, overwrite: bool) -> None:
    if overwrite:
        _remove_existing_target(target)
    _copy_tree_no_follow(source, target)


def copy_folder_batch(
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
) -> Iterator[Progress | FolderCopyResult]:
    """Copy a folder to numbered folders in a destination directory.

    All arguments, link checks, destination checks, and target expansions are validated
    synchronously before returning the iterator. When `dry_run` is `False`, creates
    `dest_dir`, acquires `target_lock` across every expanded target, copies without
    following symlinks or junctions, yields `Progress` per target, yields `FolderCopyResult`
    last, and records the run in `HistoryStore`.

    Args:
        source: Source directory to copy.
        dest_dir: Destination directory where numbered folders are placed.
        count: Number of copies to create (at least 1).
        fmt: Folder name format using `{name}` and `{n}` placeholders.
        start: Starting number for `{n}`.
        overwrite: Replace existing target paths when `True`.
        dry_run: Plan target paths without creating directories, locking, copying,
            or recording history.
        state_root: State directory for locks and run history. Defaults to `state_dir()`.
        cancel: Optional cancellation callback checked before each target copy.

    Returns:
        An iterator yielding `Progress` per target and one `FolderCopyResult` last.

    Raises:
        InputError: Invalid count, start, format, missing or non-directory source,
            link/junction path, recursive destination, path traversal, duplicate target,
            or existing target when `overwrite=False`.
    """
    if isinstance(count, bool) or count < 1:
        msg = f"Count must be at least 1, not {count}."
        raise InputError(msg)
    if isinstance(start, bool):
        msg = "Start number must be an integer."
        raise InputError(msg)
    if not str(source).strip():
        msg = "Source folder path must not be empty."
        raise InputError(msg)
    if not str(dest_dir).strip():
        msg = "Destination directory path must not be empty."
        raise InputError(msg)

    _format_target_name(fmt, source.name or "folder", start)
    _check_no_links(source)
    _check_no_links(dest_dir)

    if not source.exists() or not source.is_dir():
        msg = f"Source folder does not exist or is not a directory: {source}."
        raise InputError(msg)
    source_resolved = source.resolve()

    if dest_dir.exists() and not dest_dir.is_dir():
        msg = f"Destination path is not a directory: {dest_dir}."
        raise InputError(msg)
    dest_resolved = dest_dir.resolve(strict=False)
    if dest_resolved == source_resolved or dest_resolved.is_relative_to(source_resolved):
        msg = f"Destination directory cannot be the source folder or inside it: {dest_resolved}."
        raise InputError(msg)

    targets = _expand_targets(
        source_resolved,
        dest_resolved,
        count,
        fmt=fmt,
        start=start,
        overwrite=overwrite,
    )
    return _copy(
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
        now=_utc_now,
    )


def _copy(
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
    now: Callable[[], datetime],
) -> Iterator[Progress | FolderCopyResult]:
    started_at = now()
    arguments: dict[str, object] = {
        "source": str(source_resolved),
        "dest_dir": str(dest_resolved),
        "count": count,
        "format": fmt,
        "start": start,
        "overwrite": overwrite,
    }
    history = None if dry_run else HistoryStore(state_root=state_root)
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
                    f"Cannot create destination directory {dest_resolved}: "
                    f"{_os_error_reason(error)}."
                )
                raise OperationError(msg) from error
        lock_ctx = nullcontext() if dry_run else target_lock(targets, state_root=state_root)
        with lock_ctx:
            if not dry_run and not overwrite:
                for target in targets:
                    if target.exists() or _is_link_path(target):
                        msg = f"Target already exists: {target}. Pass overwrite=True to replace it."
                        raise InputError(msg)
            for completed, target in enumerate(targets, start=1):
                if cancel():
                    cancelled = True
                    break
                if dry_run:
                    succeeded_items.append(target)
                else:
                    try:
                        _copy_one_target(source_resolved, target, overwrite=overwrite)
                    except OSError as error:
                        failures.append(ItemFailure(target, _os_error_reason(error)))
                    else:
                        succeeded_items.append(target)
                yield Progress(completed, total, target)
    except PytoolboxError as error:
        if history is not None:
            history.record(
                "folder-copy",
                arguments,
                started_at=started_at,
                finished_at=now(),
                succeeded=len(succeeded_items),
                failed=len(failures),
                cancelled=cancelled,
                error=str(error),
            )
        raise
    except (KeyboardInterrupt, GeneratorExit):
        if history is not None:
            history.record(
                "folder-copy",
                arguments,
                started_at=started_at,
                finished_at=now(),
                succeeded=len(succeeded_items),
                failed=len(failures),
                cancelled=True,
            )
        raise

    result = FolderCopyResult(
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
            "folder-copy",
            arguments,
            started_at=started_at,
            finished_at=now(),
            succeeded=result.batch.succeeded,
            failed=len(result.batch.failures),
            cancelled=result.batch.cancelled,
        )
    yield result
