"""Merge multiple text files into a timestamped UTF-8 text file."""

import errno
from collections.abc import Callable, Iterator, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError, PytoolboxError
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, Cancel, ItemFailure, Progress

DEFAULT_SEPARATOR = "--- {name} ---"
"""Default section header format string placed before each input file's contents."""

_WIN_SHARING_VIOLATION = 32
_WIN_LOCK_VIOLATION = 33

_OS_ERROR_REASONS: dict[int, str] = {
    errno.EACCES: "access is denied",
    errno.EPERM: "permission is denied",
    errno.EBUSY: "file is busy",
    errno.EEXIST: "path already exists",
    errno.EISDIR: "the path is a directory",
    errno.ENOENT: "path was not found",
    errno.ENOSPC: "no space left on device",
    errno.ENOTDIR: "a parent path is not a directory",
    errno.EROFS: "the file system is read-only",
}


@dataclass(frozen=True)
class FileMergeResult:
    """Outcome of a `file-merge` run, yielded last.

    Attributes:
        inputs: Resolved input file paths in input order.
        output: Resolved destination file path (`merged_YYYYMMDD_HHMMSS.txt`).
        dry_run: Whether writing was skipped.
        batch: Completion summary, per-file failures, and cancellation flag.
    """

    inputs: tuple[Path, ...]
    output: Path
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


def _check_no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            msg = f"Links and junctions are not followed: {part}."
            raise InputError(msg)


def _validate_separator(separator: str) -> None:
    try:
        separator.format(name="x", path="x", stem="x", index=1)
    except (KeyError, ValueError, IndexError) as error:
        msg = f"Invalid separator format {separator!r}: {error}."
        raise InputError(msg) from error


def _decode_text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return data.decode("cp932")
        except UnicodeDecodeError:
            return None


def _read_input_text(src: Path) -> tuple[str | None, str | None]:
    try:
        if src.is_symlink() or src.is_junction():
            return None, "links and junctions are not followed"
        if not src.exists():
            return None, "path was not found"
        if src.is_dir():
            return None, "the path is a directory"
        raw = src.read_bytes()
    except OSError as error:
        return None, _os_error_reason(error)
    text = _decode_text(raw)
    if text is None:
        return None, "not UTF-8 or CP932"
    return text, None


def merge_files(
    inputs: Sequence[Path],
    output_dir: Path,
    *,
    separator: str = DEFAULT_SEPARATOR,
    overwrite: bool = False,
    dry_run: bool = False,
    state_root: Path | None = None,
    cancel: Callable[[], bool] | None = None,
    now: Callable[[], datetime] | None = None,
) -> Iterator[Progress | FileMergeResult]:
    """Merge multiple text files into a timestamped UTF-8 file (`merged_YYYYMMDD_HHMMSS.txt`).

    Parameters are validated before the iterator begins. Each input file is decoded as UTF-8
    (with or without BOM) and falls back to CP932. Unreadable inputs do not abort the run:
    an inline `[unreadable: <reason>]` notice is written under their separator header and an
    `ItemFailure` is recorded in `BatchResult.failures`.

    Args:
        inputs: One or more input file paths in merge order.
        output_dir: Destination directory where `merged_YYYYMMDD_HHMMSS.txt` is written.
        separator: Format string accepting `{name}`, `{path}`, `{stem}`, and `{index}`.
        overwrite: Allow replacing an existing destination file.
        dry_run: Inspect inputs and report the destination without locking, writing, or
            recording history.
        state_root: State directory for locks and run history. Defaults to `state_dir()`.
        cancel: Optional cancellation callback checked before each input file.
        now: Optional timezone-aware UTC clock for the output timestamp and history times.

    Returns:
        An iterator yielding `Progress` per input file and one `FileMergeResult` last.

    Raises:
        InputError: No inputs are given, any path is blank or crosses a link/junction,
            `output_dir` exists and is not a directory, `separator` is invalid, the destination
            equals an input path, or the destination already exists without `overwrite=True`.
    """
    if not inputs:
        msg = "Specify at least one input file."
        raise InputError(msg)
    if not str(output_dir).strip():
        msg = "Output directory must not be empty."
        raise InputError(msg)

    _check_no_links(output_dir)
    resolved_dir = output_dir.resolve(strict=False)
    if resolved_dir.exists() and not resolved_dir.is_dir():
        msg = f"Output directory is not a directory: {resolved_dir}."
        raise InputError(msg)

    resolved_list: list[Path] = []
    for raw_input in inputs:
        if not str(raw_input).strip():
            msg = "Input file path must not be empty."
            raise InputError(msg)
        _check_no_links(raw_input)
        resolved_list.append(raw_input.resolve(strict=False))
    resolved_inputs = tuple(resolved_list)

    _validate_separator(separator)

    active_now = _utc_now if now is None else now
    stamp = active_now().astimezone().strftime("%Y%m%d_%H%M%S")
    output = resolved_dir / f"merged_{stamp}.txt"

    if output in resolved_inputs:
        msg = f"Output path matches an input file: {output}."
        raise InputError(msg)
    if not overwrite and (output.exists() or output.is_symlink()):
        msg = f"Output already exists: {output}. Pass overwrite=True to replace it."
        raise InputError(msg)

    return _merge(
        resolved_inputs,
        resolved_dir,
        output,
        separator=separator,
        overwrite=overwrite,
        dry_run=dry_run,
        state_root=state_root,
        cancel=(lambda: False) if cancel is None else cancel,
        now=active_now,
    )


def _merge(
    resolved_inputs: tuple[Path, ...],
    resolved_dir: Path,
    output: Path,
    *,
    separator: str,
    overwrite: bool,
    dry_run: bool,
    state_root: Path | None,
    cancel: Cancel,
    now: Callable[[], datetime],
) -> Iterator[Progress | FileMergeResult]:
    started_at = now()
    arguments: dict[str, object] = {
        "inputs": [str(path) for path in resolved_inputs],
        "output_dir": str(resolved_dir),
        "output": str(output),
        "separator": separator,
        "overwrite": overwrite,
    }
    history = None if dry_run else HistoryStore(state_root=state_root)
    succeeded_inputs: list[Path] = []
    failures: list[ItemFailure] = []
    sections: list[str] = []
    cancelled = False

    try:
        if not dry_run:
            try:
                resolved_dir.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                msg = f"Cannot create output directory {resolved_dir}: {_os_error_reason(error)}."
                raise OperationError(msg) from error

        lock_ctx = nullcontext() if dry_run else target_lock(output, state_root=state_root)
        with lock_ctx:
            if not dry_run and not overwrite and (output.exists() or output.is_symlink()):
                msg = f"Output already exists: {output}. Pass overwrite=True to replace it."
                raise InputError(msg)

            total = len(resolved_inputs)
            for completed, src in enumerate(resolved_inputs, start=1):
                if cancel():
                    cancelled = True
                    break
                header = separator.format(name=src.name, path=src, stem=src.stem, index=completed)
                text, reason = _read_input_text(src)
                if reason is not None or text is None:
                    failure_reason = reason or "not UTF-8 or CP932"
                    failures.append(ItemFailure(src, failure_reason))
                    sections.append(f"{header}\n[unreadable: {failure_reason}]\n")
                else:
                    normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
                    sections.append(f"{header}\n{normalized}\n")
                    succeeded_inputs.append(src)
                yield Progress(completed, total, src)

            if not dry_run and (succeeded_inputs or failures):
                try:
                    if output.is_symlink() or output.is_junction():
                        msg = f"Links and junctions are not followed: {output}."
                        raise OperationError(msg)
                    output.write_text("\n".join(sections), encoding="utf-8", newline="\n")
                except OSError as error:
                    msg = f"Cannot write {output}: {_os_error_reason(error)}."
                    raise OperationError(msg) from error
    except PytoolboxError as error:
        if history is not None:
            history.record(
                "file-merge",
                arguments,
                started_at=started_at,
                finished_at=now(),
                succeeded=len(succeeded_inputs),
                failed=len(failures),
                cancelled=cancelled,
                error=str(error),
            )
        raise
    except (KeyboardInterrupt, GeneratorExit):
        if history is not None:
            history.record(
                "file-merge",
                arguments,
                started_at=started_at,
                finished_at=now(),
                succeeded=len(succeeded_inputs),
                failed=len(failures),
                cancelled=True,
            )
        raise

    result = FileMergeResult(
        inputs=resolved_inputs,
        output=output,
        dry_run=dry_run,
        batch=BatchResult(
            succeeded=len(succeeded_inputs),
            failures=tuple(failures),
            cancelled=cancelled,
        ),
    )
    if history is not None:
        history.record(
            "file-merge",
            arguments,
            started_at=started_at,
            finished_at=now(),
            succeeded=result.batch.succeeded,
            failed=len(result.batch.failures),
            cancelled=result.batch.cancelled,
        )
    yield result
