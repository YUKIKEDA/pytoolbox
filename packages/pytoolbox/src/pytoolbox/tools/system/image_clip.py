"""Crop images in a folder to a pixel rectangle and write `cropped_` files."""

import base64
import errno
import io
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError, PytoolboxError
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, Cancel, ItemFailure, Progress

CLIP_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".gif")
OUTPUT_PREFIX = "cropped_"
JPEG_QUALITY = 95

_FORMAT_BY_EXTENSION: dict[str, str] = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".bmp": "BMP",
    ".gif": "GIF",
}

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
class ClipBox:
    """Pixel crop rectangle `(x1, y1, x2, y2)` with left-top and right-bottom bounds.

    Attributes:
        x1: Left pixel coordinate.
        y1: Top pixel coordinate.
        x2: Right pixel coordinate (exclusive).
        y2: Bottom pixel coordinate (exclusive).
    """

    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class ClippedImageItem:
    """One cropped or planned output image.

    Attributes:
        source: Resolved source image path.
        output: Resolved destination image path.
        box: Clamped crop rectangle applied to this image.
    """

    source: Path
    output: Path
    box: ClipBox


@dataclass(frozen=True)
class ImageClipResult:
    """The result of an `image-clip` run.

    Attributes:
        input_dir: Resolved input directory path.
        output_dir: Resolved destination directory path.
        box: Requested crop rectangle before per-image clamping.
        items: Cropped or planned image items.
        dry_run: Whether writing was skipped.
        batch: Completion summary, item failures, and cancellation flag.
    """

    input_dir: Path
    output_dir: Path
    box: ClipBox
    items: tuple[ClippedImageItem, ...]
    dry_run: bool
    batch: BatchResult


def _validate_box(box: ClipBox) -> None:
    if box.x1 >= box.x2 or box.y1 >= box.y2:
        msg = (
            f"Crop box must have x1 < x2 and y1 < y2, got ({box.x1}, {box.y1}, {box.x2}, {box.y2})."
        )
        raise InputError(msg)
    if box.x2 <= 0 or box.y2 <= 0:
        msg = f"Crop box x2 and y2 must be positive, got ({box.x1}, {box.y1}, {box.x2}, {box.y2})."
        raise InputError(msg)


def parse_box(text: str) -> ClipBox:
    """Parse a comma-separated crop rectangle `"X1,Y1,X2,Y2"`.

    Args:
        text: Comma-separated integer coordinates `X1,Y1,X2,Y2`.

    Returns:
        The validated `ClipBox`.

    Raises:
        InputError: The string does not contain 4 integers or the box is non-positive/inverted.
    """
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4:
        msg = f"Box must have 4 comma-separated integers (X1,Y1,X2,Y2), not {text!r}."
        raise InputError(msg)
    coords: list[int] = []
    for part in parts:
        try:
            coords.append(int(part))
        except ValueError:
            msg = f"Box coordinates must be integers (X1,Y1,X2,Y2), not {text!r}."
            raise InputError(msg) from None
    box = ClipBox(coords[0], coords[1], coords[2], coords[3])
    _validate_box(box)
    return box


def clamp_box(box: ClipBox, width: int, height: int) -> ClipBox | None:
    """Clamp a crop rectangle to `[0, width] x [0, height]`.

    Args:
        box: Requested crop rectangle.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        The clamped `ClipBox`, or `None` if the clamped area is empty.
    """
    cx1 = max(0, min(box.x1, width))
    cy1 = max(0, min(box.y1, height))
    cx2 = max(0, min(box.x2, width))
    cy2 = max(0, min(box.y2, height))
    if cx1 >= cx2 or cy1 >= cy2:
        return None
    return ClipBox(cx1, cy1, cx2, cy2)


def _check_no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            msg = f"Links and junctions are not followed: {part}."
            raise InputError(msg)


def _validate_input_dir(input_dir: Path) -> Path:
    if not str(input_dir).strip():
        msg = "Input directory must not be empty."
        raise InputError(msg)
    _check_no_links(input_dir)
    resolved = input_dir.resolve(strict=False)
    if not resolved.exists():
        msg = f"Input directory does not exist: {resolved}."
        raise InputError(msg)
    if not resolved.is_dir():
        msg = f"Input directory is not a directory: {resolved}."
        raise InputError(msg)
    return resolved


def _validate_output_dir(input_dir: Path, output_dir: Path | None) -> Path:
    dest = input_dir if output_dir is None else output_dir
    if not str(dest).strip():
        msg = "Output directory must not be empty."
        raise InputError(msg)
    _check_no_links(dest)
    resolved = dest.resolve(strict=False)
    if resolved.exists() and not resolved.is_dir():
        msg = f"Output directory is not a directory: {resolved}."
        raise InputError(msg)
    return resolved


def _discover_candidates(resolved_input: Path, *, skip_cropped: bool) -> list[Path]:
    try:
        entries = sorted(resolved_input.iterdir(), key=lambda entry: entry.name)
    except OSError as error:
        msg = f"Cannot read input directory {resolved_input}: {_os_error_reason(error)}."
        raise InputError(msg) from error

    candidates: list[Path] = []
    for entry in entries:
        if entry.is_symlink() or entry.is_junction() or entry.is_dir():
            continue
        if skip_cropped and entry.name.startswith(OUTPUT_PREFIX):
            continue
        if entry.suffix.lower() in CLIP_EXTENSIONS and entry.is_file():
            candidates.append(entry.resolve(strict=False))
    if not candidates:
        msg = f"No supported images ({', '.join(CLIP_EXTENSIONS)}) found in {resolved_input}."
        raise InputError(msg)
    return candidates


def first_preview_image(input_dir: Path) -> tuple[Path, int, int, str]:
    """Load the first readable candidate image in `input_dir` as a preview data URI.

    Args:
        input_dir: Directory containing source images.

    Returns:
        `(image_path, width, height, data_uri)` for the first readable candidate image.

    Raises:
        InputError: `input_dir` is invalid, has no supported images, or none can be decoded.
    """
    resolved_input = _validate_input_dir(input_dir)
    candidates = _discover_candidates(resolved_input, skip_cropped=True)
    last_reason = "unreadable image"
    for candidate in candidates:
        try:
            with Image.open(candidate) as img:
                width, height = img.width, img.height
                suffix = candidate.suffix.lower()
                buffer = io.BytesIO()
                if suffix in (".jpg", ".jpeg"):
                    preview = img if img.mode in ("RGB", "L") else img.convert("RGB")
                    preview.save(buffer, format="JPEG", quality=JPEG_QUALITY)
                    mime = "image/jpeg"
                else:
                    preview = img if img.mode in ("RGB", "RGBA", "L", "P") else img.convert("RGBA")
                    preview.save(buffer, format="PNG")
                    mime = "image/png"
                encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                return candidate, width, height, f"data:{mime};base64,{encoded}"
        except OSError as error:
            last_reason = _os_error_reason(error)
    msg = f"No readable preview image found in {resolved_input}: {last_reason}."
    raise InputError(msg)


def _save_cropped(cropped: Image.Image, target: Path) -> None:
    suffix = target.suffix.lower()
    pil_format = _FORMAT_BY_EXTENSION[suffix]
    if pil_format == "JPEG":
        if cropped.mode not in ("RGB", "L"):
            cropped = cropped.convert("RGB")
        cropped.save(target, format="JPEG", quality=JPEG_QUALITY)
    else:
        cropped.save(target, format=pil_format)


def clip_images(
    input_dir: Path,
    box: ClipBox,
    *,
    output_dir: Path | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    state_root: Path | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Iterator[Progress | ImageClipResult]:
    """Crop supported images in `input_dir` to `box` and write `cropped_<name>` files.

    Args:
        input_dir: Folder containing source images (`.jpg`, `.jpeg`, `.png`, `.bmp`, `.gif`).
        box: Requested pixel crop rectangle `(x1, y1, x2, y2)`.
        output_dir: Destination folder. Defaults to `input_dir`.
        overwrite: Allow replacing existing destination files.
        dry_run: Report planned paths and clamped boxes without locking, writing, or history.
        state_root: State directory for locks and run history. Defaults to `state_dir()`.
        cancel: Optional cancellation callback checked before each image.

    Returns:
        An iterator yielding `Progress` per image and one `ImageClipResult` last.

    Raises:
        InputError: Invalid crop box, invalid input/output directory, link/junction path,
            no candidate images, or existing destination files without `overwrite=True`.
    """
    _validate_box(box)
    resolved_input = _validate_input_dir(input_dir)
    resolved_dest = _validate_output_dir(input_dir, output_dir)
    same_dir = resolved_dest == resolved_input
    candidates = _discover_candidates(resolved_input, skip_cropped=same_dir)

    planned: list[tuple[Path, Path]] = []
    for src in candidates:
        target = resolved_dest / f"{OUTPUT_PREFIX}{src.name}"
        if not overwrite and (target.exists() or target.is_symlink()):
            msg = f"Output already exists: {target}. Pass overwrite=True to replace it."
            raise InputError(msg)
        planned.append((src, target))

    return _clip(
        resolved_input,
        resolved_dest,
        box=box,
        planned=tuple(planned),
        overwrite=overwrite,
        dry_run=dry_run,
        state_root=state_root,
        cancel=(lambda: False) if cancel is None else cancel,
    )


def _clip(
    resolved_input: Path,
    resolved_dest: Path,
    *,
    box: ClipBox,
    planned: tuple[tuple[Path, Path], ...],
    overwrite: bool,
    dry_run: bool,
    state_root: Path | None,
    cancel: Cancel,
) -> Iterator[Progress | ImageClipResult]:
    started_at = datetime.now(UTC)
    arguments: dict[str, object] = {
        "input_dir": str(resolved_input),
        "output_dir": str(resolved_dest),
        "box": [box.x1, box.y1, box.x2, box.y2],
        "overwrite": overwrite,
    }
    history = None if dry_run else HistoryStore(state_root=state_root)
    succeeded_items: list[ClippedImageItem] = []
    failures: list[ItemFailure] = []
    cancelled = False
    targets = [target for _src, target in planned]

    try:
        if not dry_run:
            try:
                resolved_dest.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                msg = f"Cannot create output directory {resolved_dest}: {_os_error_reason(error)}."
                raise OperationError(msg) from error

        lock_ctx = nullcontext() if dry_run else target_lock(targets, state_root=state_root)
        with lock_ctx:
            if not dry_run and not overwrite:
                for target in targets:
                    if target.exists() or target.is_symlink():
                        msg = f"Output already exists: {target}. Pass overwrite=True to replace it."
                        raise InputError(msg)
            total = len(planned)
            for completed, (src, target) in enumerate(planned, start=1):
                if cancel():
                    cancelled = True
                    break
                try:
                    with Image.open(src) as img:
                        width, height = img.width, img.height
                        clamped = clamp_box(box, width, height)
                        if clamped is None:
                            failures.append(
                                ItemFailure(
                                    src,
                                    f"crop box is outside image bounds ({width}x{height})",
                                )
                            )
                        elif dry_run:
                            succeeded_items.append(ClippedImageItem(src, target, clamped))
                        elif target.is_symlink() or target.is_junction():
                            failures.append(ItemFailure(target, "target is a link or junction"))
                        elif target.is_dir():
                            failures.append(ItemFailure(target, "the path is a directory"))
                        else:
                            try:
                                cropped = img.crop((clamped.x1, clamped.y1, clamped.x2, clamped.y2))
                                _save_cropped(cropped, target)
                                succeeded_items.append(ClippedImageItem(src, target, clamped))
                            except OSError as error:
                                failures.append(ItemFailure(target, _os_error_reason(error)))
                except OSError as error:
                    failures.append(ItemFailure(src, _os_error_reason(error)))
                yield Progress(completed, total, target)
    except PytoolboxError as error:
        if history is not None:
            history.record(
                "image-clip",
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
                "image-clip",
                arguments,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                succeeded=len(succeeded_items),
                failed=len(failures),
                cancelled=True,
            )
        raise

    result = ImageClipResult(
        input_dir=resolved_input,
        output_dir=resolved_dest,
        box=box,
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
            "image-clip",
            arguments,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            succeeded=result.batch.succeeded,
            failed=len(result.batch.failures),
            cancelled=result.batch.cancelled,
        )
    yield result
