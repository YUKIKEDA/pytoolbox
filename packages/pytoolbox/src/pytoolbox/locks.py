"""Per-target advisory file locks for write and delete tools."""

import errno
import hashlib
import sys
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

from pytoolbox.errors import OperationError
from pytoolbox.state import state_dir

_LOCK_CONFLICT_ERRNOS = frozenset(
    code
    for name in ("EACCES", "EAGAIN", "EWOULDBLOCK", "EDEADLK", "EDEADLOCK")
    if (code := getattr(errno, name, None)) is not None
)

_OS_ERROR_REASONS: dict[int, str] = {
    errno.EACCES: "access is denied",
    errno.ENOENT: "path was not found",
    errno.ENOTDIR: "a parent path is not a directory",
    errno.EISDIR: "the path is a directory",
    errno.EEXIST: "the path already exists",
    errno.EROFS: "the file system is read-only",
}

if sys.platform == "win32":
    import msvcrt

    def _lock_fd(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_fd(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_fd(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_fd(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _os_error_reason(error: OSError) -> str:
    """Say in English why an OS file operation failed."""
    if error.errno is not None and error.errno in _OS_ERROR_REASONS:
        return _OS_ERROR_REASONS[error.errno]
    return error.strerror or str(error)


def _is_lock_conflict(error: OSError) -> bool:
    return isinstance(error, BlockingIOError | PermissionError) or (
        error.errno in _LOCK_CONFLICT_ERRNOS
    )


def lock_path(
    target: Path,
    *,
    state_root: Path | None = None,
    platform: str | None = None,
) -> Path:
    """Return the lock file path for a target path.

    Args:
        target: Write or delete target to derive the lock name from.
        state_root: State directory root. Defaults to ``state_dir()``.
        platform: Platform name such as ``"win32"``. Defaults to ``sys.platform``.

    Returns:
        ``<state>/locks/<sha256>.lock`` for the resolved target.
    """
    resolved = target.resolve(strict=False)
    active_platform = sys.platform if platform is None else platform
    key = str(resolved).lower() if active_platform == "win32" else str(resolved)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    root = state_dir() if state_root is None else state_root
    return root / "locks" / f"{digest}.lock"


def _normalize_targets(
    targets: Path | Iterable[Path],
    *,
    state_root: Path | None,
) -> list[tuple[Path, Path]]:
    raw = (targets,) if isinstance(targets, Path) else targets
    by_name: dict[str, tuple[Path, Path]] = {}
    for target in raw:
        resolved = target.resolve(strict=False)
        file_path = lock_path(resolved, state_root=state_root)
        by_name.setdefault(file_path.name, (file_path, resolved))
    return [by_name[name] for name in sorted(by_name)]


def _acquire_one(stack: ExitStack, lock_file: Path, resolved: Path) -> None:
    try:
        handle = stack.enter_context(lock_file.open("a+b"))
    except OSError as error:
        msg = f"Cannot open lock file {lock_file}: {_os_error_reason(error)}."
        raise OperationError(msg) from error
    try:
        _lock_fd(handle)
    except OSError as error:
        if _is_lock_conflict(error):
            msg = f"Target is locked by another run: {resolved}."
        else:
            msg = f"Cannot lock {resolved}: {_os_error_reason(error)}."
        raise OperationError(msg) from error
    stack.callback(_unlock_fd, handle)


@contextmanager
def target_lock(
    targets: Path | Iterable[Path],
    *,
    state_root: Path | None = None,
) -> Iterator[tuple[Path, ...]]:
    """Acquire non-blocking OS file locks for one or more targets.

    Targets are resolved, deduplicated, and locked in sorted lock-filename order.
    Lock files are left on disk after unlocking. If any lock cannot be acquired,
    previously acquired locks are released and ``OperationError`` is raised.

    Args:
        targets: Single target path or an iterable of target paths.
        state_root: State directory root. Defaults to ``state_dir()``.

    Yields:
        Tuple of the acquired lock file paths in acquisition order.

    Raises:
        OperationError: One of the targets is locked by another run or the lock
            file cannot be opened.
    """
    entries = _normalize_targets(targets, state_root=state_root)
    if not entries:
        yield ()
        return

    locks_dir = entries[0][0].parent
    try:
        locks_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        msg = f"Cannot prepare lock directory {locks_dir}: {_os_error_reason(error)}."
        raise OperationError(msg) from error

    with ExitStack() as stack:
        for lock_file, resolved in entries:
            _acquire_one(stack, lock_file, resolved)
        yield tuple(lock_file for lock_file, _ in entries)
