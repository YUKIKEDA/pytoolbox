"""Cleanup of named user caches."""

import errno
import os
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, PytoolboxError
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, Cancel, ItemFailure, Progress

CACHE_TARGETS: tuple[str, ...] = ("temp", "pip", "uv", "npm", "nuget", "cargo")
"""Supported cache target names in display order."""

TEMP_MAX_AGE_SECONDS = 86400
"""Minimum age in seconds (24 hours) for entries in `temp` to be removed."""

_WIN_SHARING_VIOLATION = 32
_WIN_LOCK_VIOLATION = 33

_OS_ERROR_REASONS: dict[int, str] = {
    errno.EACCES: "access is denied",
    errno.EPERM: "permission is denied",
    errno.EBUSY: "file is busy",
    errno.ENOENT: "path was not found",
    errno.ENOTDIR: "a parent path is not a directory",
    errno.EISDIR: "the path is a directory",
    errno.EROFS: "the file system is read-only",
}


@dataclass(frozen=True)
class CacheItem:
    """One cache file planned for deletion or deleted.

    Attributes:
        target: Cache target name, such as `temp` or `pip`.
        path: File path inside the cache root.
        bytes: File size in bytes.
    """

    target: str
    path: Path
    bytes: int


@dataclass(frozen=True)
class CacheCleanResult:
    """Outcome of a cache-clean run, yielded last.

    Attributes:
        targets: Selected cache target names, deduplicated in input order.
        items: Files planned for deletion (when `dry_run`) or deleted (when applied).
        total_bytes: Total size in bytes across `items`.
        dry_run: Whether the run inspected caches without deleting.
        batch: Summary counts, per-item failures, and cancellation flag.
    """

    targets: tuple[str, ...]
    items: tuple[CacheItem, ...]
    total_bytes: int
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


def resolve_targets(targets: Iterable[str]) -> tuple[str, ...]:
    """Validate and deduplicate cache target names while preserving order.

    Args:
        targets: Candidate target names.

    Returns:
        Deduplicated canonical target names in input order.

    Raises:
        InputError: No target is given, an entry is blank, or any name is unknown.
    """
    selected: list[str] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for raw in targets:
        name = raw.strip().lower()
        if not name:
            msg = "Cache target name must not be empty."
            raise InputError(msg)
        if name not in CACHE_TARGETS:
            if name not in unknown:
                unknown.append(name)
            continue
        if name not in seen:
            seen.add(name)
            selected.append(name)
    if unknown:
        allowed = ", ".join(CACHE_TARGETS)
        msg = f"Unknown cache target: {', '.join(unknown)}. Choose from {allowed}."
        raise InputError(msg)
    if not selected:
        msg = f"Specify at least one cache target: {', '.join(CACHE_TARGETS)}."
        raise InputError(msg)
    return tuple(selected)


def default_cache_roots(
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> dict[str, Path]:
    r"""Return the default cache directory path for each supported target.

    On Windows (or when `LOCALAPPDATA` / `USERPROFILE` are set in `env`), resolves:
    `temp` (`%LOCALAPPDATA%\Temp`), `pip` (`%LOCALAPPDATA%\pip\Cache`),
    `uv` (`%LOCALAPPDATA%\uv\cache`), `npm` (`%LOCALAPPDATA%\npm-cache`),
    `nuget` (`%USERPROFILE%\.nuget\packages`), and `cargo` (`%USERPROFILE%\.cargo\registry`).
    Elsewhere, falls back to `$XDG_CACHE_HOME` / `~/.cache` and `home`.

    Args:
        env: Environment variables to read. Defaults to `os.environ`.
        platform: Platform string such as `"win32"`. Defaults to `sys.platform`.
        home: Home directory fallback. Defaults to `Path.home()`.

    Returns:
        Mapping from each target name in `CACHE_TARGETS` to its root directory.
    """
    active_env = os.environ if env is None else env
    active_platform = sys.platform if platform is None else platform
    active_home = Path.home() if home is None else home

    local_app_data = active_env.get("LOCALAPPDATA")
    user_profile = active_env.get("USERPROFILE")
    profile_root = Path(user_profile) if user_profile else active_home

    if local_app_data:
        local_root = Path(local_app_data)
        temp_root = local_root / "Temp"
        pip_root = local_root / "pip" / "Cache"
        uv_root = local_root / "uv" / "cache"
        npm_root = local_root / "npm-cache"
    elif active_platform == "win32":
        local_root = active_home / "AppData" / "Local"
        temp_root = local_root / "Temp"
        pip_root = local_root / "pip" / "Cache"
        uv_root = local_root / "uv" / "cache"
        npm_root = local_root / "npm-cache"
    else:
        xdg_cache = active_env.get("XDG_CACHE_HOME")
        cache_root = Path(xdg_cache) if xdg_cache else active_home / ".cache"
        temp_root = cache_root / "tmp"
        pip_root = cache_root / "pip"
        uv_root = cache_root / "uv"
        npm_root = active_home / ".npm"

    return {
        "temp": temp_root,
        "pip": pip_root,
        "uv": uv_root,
        "npm": npm_root,
        "nuget": profile_root / ".nuget" / "packages",
        "cargo": profile_root / ".cargo" / "registry",
    }


def _resolve_target_roots(
    selected: tuple[str, ...],
    *,
    roots: Mapping[str, Path] | None,
    env: Mapping[str, str] | None,
    home: Path | None,
) -> dict[str, Path]:
    if roots is not None and all(target in roots for target in selected):
        return {target: roots[target] for target in selected}
    defaults = default_cache_roots(env=env, home=home)
    overrides: Mapping[str, Path] = {} if roots is None else roots
    return {target: overrides.get(target, defaults[target]) for target in selected}


def _scan_one_root(
    target: str,
    root: Path,
    now_ts: float,
    cancel: Cancel,
) -> tuple[list[CacheItem], list[Path], list[ItemFailure], bool]:
    items: list[CacheItem] = []
    prune_dirs: list[Path] = []
    failures: list[ItemFailure] = []
    try:
        if _is_link_path(root) or not root.exists():
            return items, prune_dirs, failures, False
        if not root.is_dir():
            failures.append(ItemFailure(root, "Path is not a directory."))
            return items, prune_dirs, failures, False
    except OSError as error:
        failures.append(ItemFailure(root, _os_error_reason(error)))
        return items, prune_dirs, failures, False

    pending: list[Path] = [root]
    while pending:
        if cancel():
            return items, prune_dirs, failures, True
        folder = pending.pop()
        try:
            with os.scandir(folder) as scan:
                entries: list[os.DirEntry[str]] = list(scan)
        except OSError as error:
            failures.append(ItemFailure(folder, _os_error_reason(error)))
            continue
        entries.sort(key=lambda entry: entry.name.casefold())
        subdirs: list[Path] = []
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                if _is_link_entry(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if target == "temp":
                        mtime = entry.stat(follow_symlinks=False).st_mtime
                        if now_ts - mtime >= TEMP_MAX_AGE_SECONDS:
                            prune_dirs.append(entry_path)
                    else:
                        prune_dirs.append(entry_path)
                    subdirs.append(entry_path)
                elif entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    if target == "temp" and now_ts - stat.st_mtime < TEMP_MAX_AGE_SECONDS:
                        continue
                    items.append(CacheItem(target, entry_path, stat.st_size))
            except OSError as error:
                failures.append(ItemFailure(entry_path, _os_error_reason(error)))
        pending.extend(reversed(subdirs))
    return items, prune_dirs, failures, False


def _scan_targets(
    selected: tuple[str, ...],
    target_roots: Mapping[str, Path],
    now_ts: float,
    cancel: Cancel,
) -> tuple[list[CacheItem], list[Path], list[ItemFailure], bool]:
    items: list[CacheItem] = []
    prune_dirs: list[Path] = []
    failures: list[ItemFailure] = []
    for target in selected:
        found_items, found_dirs, found_failures, cancelled = _scan_one_root(
            target, target_roots[target], now_ts, cancel
        )
        items.extend(found_items)
        prune_dirs.extend(found_dirs)
        failures.extend(found_failures)
        if cancelled:
            return items, prune_dirs, failures, True
    return items, prune_dirs, failures, False


def _prune_empty_dirs(prune_dirs: Iterable[Path]) -> None:
    """Remove empty subdirectories deepest-first, keeping roots and non-empty dirs."""
    for folder in sorted(prune_dirs, key=lambda path: len(path.parts), reverse=True):
        try:
            if _is_link_path(folder) or not folder.is_dir():
                continue
            with os.scandir(folder) as scan:
                if any(scan):
                    continue
            folder.rmdir()
        except OSError:
            continue


def clean_caches(
    targets: Iterable[str],
    *,
    apply: bool = False,
    roots: Mapping[str, Path] | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    state_root: Path | None = None,
    cancel: Cancel | None = None,
    now: Callable[[], datetime] | None = None,
) -> Iterator[Progress | CacheCleanResult]:
    """Inspect or delete selected user caches.

    Target names are validated before any directory is scanned or locked. By default
    (`apply=False`), nothing is deleted, no locks are acquired, and no history entry is
    written. When `apply=True`, locks are held on the selected cache roots via
    `target_lock`, eligible files are deleted, and the run is recorded in `HistoryStore`.

    Args:
        targets: One or more names from `CACHE_TARGETS`.
        apply: Delete eligible contents when `True`; dry-run when `False`.
        roots: Optional explicit directory mapping overriding default target paths.
        env: Environment mapping passed to `default_cache_roots`.
        home: Home directory passed to `default_cache_roots`.
        state_root: State directory for locks and run history. Defaults to `state_dir()`.
        cancel: Optional cancellation callback checked between directories and files.
        now: Optional timezone-aware UTC clock used for `temp` age and history times.

    Returns:
        An iterator yielding `Progress` per candidate file and one `CacheCleanResult` last.

    Raises:
        InputError: No target is given, an entry is blank, or any target name is unknown.
    """
    selected = resolve_targets(targets)
    target_roots = _resolve_target_roots(selected, roots=roots, env=env, home=home)
    return _clean(
        selected,
        target_roots,
        apply=apply,
        state_root=state_root,
        cancel=(lambda: False) if cancel is None else cancel,
        now=_utc_now if now is None else now,
    )


def _clean(
    selected: tuple[str, ...],
    target_roots: Mapping[str, Path],
    *,
    apply: bool,
    state_root: Path | None,
    cancel: Cancel,
    now: Callable[[], datetime],
) -> Iterator[Progress | CacheCleanResult]:
    started_at = now()
    arguments: dict[str, object] = {"targets": list(selected), "apply": apply}
    history = HistoryStore(state_root=state_root) if apply else None
    succeeded_items: list[CacheItem] = []
    failures: list[ItemFailure] = []
    cancelled = False

    try:
        lock_ctx = (
            target_lock([target_roots[target] for target in selected], state_root=state_root)
            if apply
            else nullcontext()
        )
        with lock_ctx:
            candidates, prune_dirs, scan_failures, cancelled = _scan_targets(
                selected, target_roots, now().timestamp(), cancel
            )
            failures.extend(scan_failures)
            total = len(candidates)
            if not cancelled:
                for completed, item in enumerate(candidates, start=1):
                    if cancel():
                        cancelled = True
                        break
                    if apply:
                        try:
                            if _is_link_path(item.path):
                                continue
                            item.path.unlink()
                        except OSError as error:
                            failures.append(ItemFailure(item.path, _os_error_reason(error)))
                        else:
                            succeeded_items.append(item)
                    else:
                        succeeded_items.append(item)
                    yield Progress(completed, total, item.path)
                if apply and not cancelled:
                    _prune_empty_dirs(prune_dirs)
    except PytoolboxError as error:
        if history is not None:
            history.record(
                "cache-clean",
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
                "cache-clean",
                arguments,
                started_at=started_at,
                finished_at=now(),
                succeeded=len(succeeded_items),
                failed=len(failures),
                cancelled=True,
            )
        raise

    result = CacheCleanResult(
        targets=selected,
        items=tuple(succeeded_items),
        total_bytes=sum(item.bytes for item in succeeded_items),
        dry_run=not apply,
        batch=BatchResult(
            succeeded=len(succeeded_items),
            failures=tuple(failures),
            cancelled=cancelled,
        ),
    )
    if history is not None:
        history.record(
            "cache-clean",
            arguments,
            started_at=started_at,
            finished_at=now(),
            succeeded=result.batch.succeeded,
            failed=len(result.batch.failures),
            cancelled=result.batch.cancelled,
        )
    yield result
