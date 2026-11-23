"""Split a PDF file into one PDF per page."""

import errno
import io
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError, PytoolboxError
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, Cancel, ItemFailure, Progress

DEFAULT_FORMAT = "page_{n}.pdf"

_OS_ERROR_REASONS: dict[int, str] = {
    errno.EACCES: "access is denied",
    errno.EEXIST: "path already exists",
    errno.EISDIR: "the path is a directory",
    errno.ENOENT: "path was not found",
    errno.ENOSPC: "no space left on device",
    errno.ENOTDIR: "a parent path is not a directory",
    errno.EROFS: "the file system is read-only",
}


def _os_error_reason(error: OSError) -> str:
    if error.errno is not None and error.errno in _OS_ERROR_REASONS:
        return _OS_ERROR_REASONS[error.errno]
    return error.strerror or str(error)


@dataclass(frozen=True)
class PdfSplitResult:
    """The result of a `pdf-split` run.

    Attributes:
        source: Resolved input PDF file path.
        output_dir: Resolved destination directory path.
        items: Planned or written page PDF file paths.
        dry_run: Whether writing was skipped.
        batch: Completion summary, item failures, and cancellation flag.
    """

    source: Path
    output_dir: Path
    items: tuple[Path, ...]
    dry_run: bool
    batch: BatchResult


def _check_no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            msg = f"Links and junctions are not followed: {part}."
            raise InputError(msg)


def _check_no_intermediate_links(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or current.is_junction():
            msg = f"Links and junctions are not followed: {current}."
            raise InputError(msg)


def _expand_targets(source: Path, output_dir: Path, fmt: str, page_count: int) -> tuple[Path, ...]:
    if not fmt.strip():
        msg = "Format must not be empty."
        raise InputError(msg)

    targets: list[Path] = []
    seen: set[Path] = set()
    for page_num in range(1, page_count + 1):
        try:
            rel_text = fmt.format(n=page_num, stem=source.stem, name=source.name)
        except (IndexError, KeyError, ValueError) as error:
            msg = f"Invalid filename format {fmt!r}: {error}."
            raise InputError(msg) from error

        if not rel_text.strip() or rel_text.endswith(("/", "\\")):
            msg = f"Invalid output name from format {fmt!r}: {rel_text!r}."
            raise InputError(msg)

        rel_path = Path(rel_text)
        if (
            rel_path.is_absolute()
            or rel_path.drive
            or any(part in ("", ".", "..") for part in rel_path.parts)
        ):
            msg = f"Output path escapes destination folder: {rel_text!r}."
            raise InputError(msg)

        _check_no_intermediate_links(output_dir, rel_path)
        resolved_parent = (output_dir / rel_path.parent).resolve(strict=False)
        candidate = resolved_parent / rel_path.name
        if not resolved_parent.is_relative_to(output_dir) or candidate == output_dir:
            msg = f"Output path escapes destination folder: {rel_text!r}."
            raise InputError(msg)
        if candidate == source:
            msg = f"Output path must not overwrite the source file: {candidate}."
            raise InputError(msg)
        if candidate in seen:
            msg = f"Format produces duplicate output paths: {candidate}."
            raise InputError(msg)
        seen.add(candidate)
        targets.append(candidate)

    return tuple(targets)


def split_pdf(
    source: Path,
    output_dir: Path,
    *,
    fmt: str = DEFAULT_FORMAT,
    overwrite: bool = False,
    dry_run: bool = False,
    state_root: Path | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Iterator[Progress | PdfSplitResult]:
    """Split a PDF file into numbered single-page PDF files.

    Args:
        source: Input PDF file path.
        output_dir: Destination folder where split page PDFs are written.
        fmt: Output filename format using `{n}`, `{stem}`, and `{name}`.
        overwrite: Allow replacing existing output files.
        dry_run: Report planned paths without locking, writing files, or recording history.
        state_root: State directory for locks and run history. Defaults to `state_dir()`.
        cancel: Optional cancellation callback checked before each page.

    Returns:
        An iterator yielding `Progress` per page and one `PdfSplitResult` last.

    Raises:
        InputError: Missing or invalid path, link/junction path, invalid/empty/encrypted PDF,
            invalid format, path traversal, duplicate paths, or existing targets without overwrite.
        OperationError: Source file cannot be read into memory.
    """
    if not str(source).strip():
        msg = "Source PDF path must not be empty."
        raise InputError(msg)
    if not str(output_dir).strip():
        msg = "Output directory must not be empty."
        raise InputError(msg)

    _check_no_links(source)
    _check_no_links(output_dir)

    resolved_source = source.resolve(strict=False)
    if not resolved_source.exists():
        msg = f"Source PDF was not found: {resolved_source}."
        raise InputError(msg)
    if not resolved_source.is_file():
        msg = f"Source PDF is not a regular file: {resolved_source}."
        raise InputError(msg)

    resolved_dir = output_dir.resolve(strict=False)
    if resolved_dir.exists() and not resolved_dir.is_dir():
        msg = f"Output directory is not a directory: {resolved_dir}."
        raise InputError(msg)

    try:
        payload = resolved_source.read_bytes()
    except OSError as error:
        msg = f"Cannot read {resolved_source}: {_os_error_reason(error)}."
        raise OperationError(msg) from error

    page_count = 0
    try:
        reader = PdfReader(io.BytesIO(payload), strict=False)
        if not reader.is_encrypted:
            page_count = len(reader.pages)
    except PyPdfError as error:
        msg = f"Invalid PDF file {resolved_source}: {error}."
        raise InputError(msg) from error

    if reader.is_encrypted:
        msg = f"Encrypted PDF files are not supported: {resolved_source}."
        raise InputError(msg)

    if page_count < 1:
        msg = f"PDF has no pages: {resolved_source}."
        raise InputError(msg)

    targets = _expand_targets(resolved_source, resolved_dir, fmt, page_count)
    if not overwrite:
        for target in targets:
            if target.exists() or target.is_symlink():
                msg = f"Output already exists: {target}. Pass overwrite=True to replace it."
                raise InputError(msg)

    return _split(
        resolved_source,
        resolved_dir,
        reader=reader,
        targets=targets,
        fmt=fmt,
        overwrite=overwrite,
        dry_run=dry_run,
        state_root=state_root,
        cancel=(lambda: False) if cancel is None else cancel,
    )


def _split(
    resolved_source: Path,
    resolved_dir: Path,
    *,
    reader: PdfReader,
    targets: tuple[Path, ...],
    fmt: str,
    overwrite: bool,
    dry_run: bool,
    state_root: Path | None,
    cancel: Cancel,
) -> Iterator[Progress | PdfSplitResult]:
    started_at = datetime.now(UTC)
    arguments: dict[str, object] = {
        "source": str(resolved_source),
        "output_dir": str(resolved_dir),
        "format": fmt,
        "pages": len(targets),
        "overwrite": overwrite,
    }
    history = None if dry_run else HistoryStore(state_root=state_root)
    succeeded_items: list[Path] = []
    failures: list[ItemFailure] = []
    cancelled = False

    try:
        if not dry_run:
            try:
                resolved_dir.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                msg = f"Cannot create output directory {resolved_dir}: {_os_error_reason(error)}."
                raise OperationError(msg) from error

        lock_ctx = nullcontext() if dry_run else target_lock(targets, state_root=state_root)
        with lock_ctx:
            if not dry_run and not overwrite:
                for target in targets:
                    if target.exists() or target.is_symlink():
                        msg = f"Output already exists: {target}. Pass overwrite=True to replace it."
                        raise InputError(msg)

            total = len(targets)
            for completed, target in enumerate(targets, start=1):
                if cancel():
                    cancelled = True
                    break
                if dry_run:
                    succeeded_items.append(target)
                else:
                    try:
                        if target.is_symlink() or target.is_junction():
                            failures.append(ItemFailure(target, "target is a link or junction"))
                        elif target.is_dir():
                            failures.append(ItemFailure(target, "the path is a directory"))
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            writer = PdfWriter()
                            writer.add_page(reader.pages[completed - 1])
                            out_buf = io.BytesIO()
                            writer.write(out_buf)
                            target.write_bytes(out_buf.getvalue())
                            succeeded_items.append(target)
                    except (OSError, PyPdfError) as error:
                        reason = (
                            _os_error_reason(error) if isinstance(error, OSError) else str(error)
                        )
                        failures.append(ItemFailure(target, reason))
                yield Progress(completed, total, target)
    except PytoolboxError as error:
        if history is not None:
            history.record(
                "pdf-split",
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
                "pdf-split",
                arguments,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                succeeded=len(succeeded_items),
                failed=len(failures),
                cancelled=True,
            )
        raise

    result = PdfSplitResult(
        source=resolved_source,
        output_dir=resolved_dir,
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
            "pdf-split",
            arguments,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            succeeded=result.batch.succeeded,
            failed=len(result.batch.failures),
            cancelled=result.batch.cancelled,
        )
    yield result
