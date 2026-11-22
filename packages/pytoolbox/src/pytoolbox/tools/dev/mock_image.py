"""Synthetic mock image file generation."""

import errno
import io
import random
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError, PytoolboxError
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, Cancel, ItemFailure, Progress

IMAGE_FORMATS: tuple[str, ...] = ("png", "jpg")
DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 480
DEFAULT_COUNT = 3

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
class MockImageItem:
    """One generated or planned mock image file.

    Attributes:
        path: Resolved destination file path.
        width: Image width in pixels.
        height: Image height in pixels.
        bytes: File size in bytes (`0` on dry-run).
    """

    path: Path
    width: int
    height: int
    bytes: int


@dataclass(frozen=True)
class MockImageResult:
    """The result of a `mock-image` run.

    Attributes:
        output_dir: Resolved destination directory path.
        format: Normalized output image format (`png` or `jpg`).
        items: Generated or planned mock image items.
        seed: Random seed used, or `None` when non-deterministic.
        random_ratio: Whether each image randomized its aspect ratio within bounds.
        dry_run: Whether writing was skipped.
        batch: Completion summary, item failures, and cancellation flag.
    """

    output_dir: Path
    format: str
    items: tuple[MockImageItem, ...]
    seed: int | None
    random_ratio: bool
    dry_run: bool
    batch: BatchResult


def _check_no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            msg = f"Links and junctions are not followed: {part}."
            raise InputError(msg)


def _normalize_format(image_format: str) -> str:
    fmt = image_format.strip().lower()
    if fmt not in IMAGE_FORMATS:
        msg = f"Unknown image format {image_format!r}. Choose from: {', '.join(IMAGE_FORMATS)}."
        raise InputError(msg)
    return fmt


def _render_image(width: int, height: int, image_format: str, rng: random.Random) -> bytes:
    background = (rng.randint(36, 196), rng.randint(36, 196), rng.randint(36, 196))
    luminance = 0.299 * background[0] + 0.587 * background[1] + 0.114 * background[2]
    foreground = (16, 16, 16) if luminance > 140 else (245, 245, 245)

    image = Image.new("RGB", (width, height), color=background)
    draw = ImageDraw.Draw(image)
    label = f"{width}x{height}"
    font_size = max(8, min(width, height) // 6)
    font = ImageFont.load_default(size=font_size)
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    text_x = (width - (right - left)) / 2 - left
    text_y = (height - (bottom - top)) / 2 - top
    draw.text((text_x, text_y), label, fill=foreground, font=font)

    buffer = io.BytesIO()
    if image_format == "jpg":
        image.save(buffer, format="JPEG", quality=90)
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_mock_images(
    output_dir: Path,
    width: int,
    height: int,
    count: int,
    *,
    random_ratio: bool = False,
    image_format: str = "png",
    seed: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    state_root: Path | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Iterator[Progress | MockImageResult]:
    """Generate numbered PNG or JPEG mock images with dimension overlays.

    Args:
        output_dir: Destination folder where `mock_001.<ext>` files are written.
        width: Image width in pixels (upper bound when `random_ratio=True`, at least 1).
        height: Image height in pixels (upper bound when `random_ratio=True`, at least 1).
        count: Number of images to generate (at least 1).
        random_ratio: Randomize each image's dimensions up to `width` and `height`.
        image_format: Output format (`png` or `jpg`).
        seed: Optional random seed for deterministic sizes and colors.
        overwrite: Allow replacing existing destination files.
        dry_run: Report planned paths without locking, writing files, or recording history.
        state_root: State directory for locks and run history. Defaults to `state_dir()`.
        cancel: Optional cancellation callback checked before each image.

    Returns:
        An iterator yielding `Progress` per image and one `MockImageResult` last.

    Raises:
        InputError: Invalid dimensions, count, or format, link/junction path,
            non-directory destination path, or existing targets without `overwrite=True`.
    """
    if width < 1:
        msg = f"Width must be at least 1, not {width}."
        raise InputError(msg)
    if height < 1:
        msg = f"Height must be at least 1, not {height}."
        raise InputError(msg)
    if count < 1:
        msg = f"Count must be at least 1, not {count}."
        raise InputError(msg)
    if not str(output_dir).strip():
        msg = "Output directory must not be empty."
        raise InputError(msg)

    fmt = _normalize_format(image_format)
    _check_no_links(output_dir)
    resolved_dir = output_dir.resolve(strict=False)
    if resolved_dir.exists() and not resolved_dir.is_dir():
        msg = f"Output directory is not a directory: {resolved_dir}."
        raise InputError(msg)

    rng = random.Random(seed)  # noqa: S311 - synthetic test image generator, not cryptographic
    planned: list[tuple[Path, int, int]] = []
    for index in range(1, count + 1):
        target = resolved_dir / f"mock_{index:03d}.{fmt}"
        if not overwrite and (target.exists() or target.is_symlink()):
            msg = f"Output already exists: {target}. Pass overwrite=True to replace it."
            raise InputError(msg)
        if random_ratio:
            item_w = rng.randint(max(1, width // 4), width)
            item_h = rng.randint(max(1, height // 4), height)
        else:
            item_w, item_h = width, height
        planned.append((target, item_w, item_h))

    return _generate(
        resolved_dir,
        width=width,
        height=height,
        count=count,
        fmt=fmt,
        random_ratio=random_ratio,
        seed=seed,
        overwrite=overwrite,
        dry_run=dry_run,
        planned=tuple(planned),
        rng=rng,
        state_root=state_root,
        cancel=(lambda: False) if cancel is None else cancel,
    )


def _generate(
    resolved_dir: Path,
    *,
    width: int,
    height: int,
    count: int,
    fmt: str,
    random_ratio: bool,
    seed: int | None,
    overwrite: bool,
    dry_run: bool,
    planned: tuple[tuple[Path, int, int], ...],
    rng: random.Random,
    state_root: Path | None,
    cancel: Cancel,
) -> Iterator[Progress | MockImageResult]:
    started_at = datetime.now(UTC)
    arguments: dict[str, object] = {
        "output_dir": str(resolved_dir),
        "width": width,
        "height": height,
        "count": count,
        "random_ratio": random_ratio,
        "format": fmt,
        "seed": seed,
        "overwrite": overwrite,
    }
    history = None if dry_run else HistoryStore(state_root=state_root)
    succeeded_items: list[MockImageItem] = []
    failures: list[ItemFailure] = []
    cancelled = False
    targets = [target for target, _w, _h in planned]

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
            total = len(planned)
            for completed, (target, item_w, item_h) in enumerate(planned, start=1):
                if cancel():
                    cancelled = True
                    break
                if dry_run:
                    succeeded_items.append(MockImageItem(target, item_w, item_h, 0))
                else:
                    try:
                        if target.is_symlink() or target.is_junction():
                            failures.append(ItemFailure(target, "target is a link or junction"))
                        elif target.is_dir():
                            failures.append(ItemFailure(target, "the path is a directory"))
                        else:
                            payload = _render_image(item_w, item_h, fmt, rng)
                            target.write_bytes(payload)
                            succeeded_items.append(
                                MockImageItem(target, item_w, item_h, len(payload))
                            )
                    except OSError as error:
                        failures.append(ItemFailure(target, _os_error_reason(error)))
                yield Progress(completed, total, target)
    except PytoolboxError as error:
        if history is not None:
            history.record(
                "mock-image",
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
                "mock-image",
                arguments,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                succeeded=len(succeeded_items),
                failed=len(failures),
                cancelled=True,
            )
        raise

    result = MockImageResult(
        output_dir=resolved_dir,
        format=fmt,
        items=tuple(succeeded_items),
        seed=seed,
        random_ratio=random_ratio,
        dry_run=dry_run,
        batch=BatchResult(
            succeeded=len(succeeded_items),
            failures=tuple(failures),
            cancelled=cancelled,
        ),
    )
    if history is not None:
        history.record(
            "mock-image",
            arguments,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            succeeded=result.batch.succeeded,
            failed=len(result.batch.failures),
            cancelled=result.batch.cancelled,
        )
    yield result
