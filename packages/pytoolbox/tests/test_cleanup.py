import errno
import os
import sys
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

if sys.platform == "win32":
    import _winapi

import pytest

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, ItemFailure, Progress
from pytoolbox.tools.system import cleanup
from pytoolbox.tools.system.cleanup import (
    CACHE_TARGETS,
    TEMP_MAX_AGE_SECONDS,
    CacheCleanResult,
    CacheItem,
    clean_caches,
    default_cache_roots,
    resolve_targets,
)

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return NOW


def _write(path: Path, data: bytes, *, age_seconds: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if age_seconds is not None:
        ts = (NOW - timedelta(seconds=age_seconds)).timestamp()
        os.utime(path, (ts, ts))
    return path


def _roots(base: Path) -> dict[str, Path]:
    return {name: base / "caches" / name for name in CACHE_TARGETS}


def test_resolve_targets_validates_deduplicates_and_preserves_order() -> None:
    assert resolve_targets([" pip ", "TEMP", "pip", "cargo"]) == ("pip", "temp", "cargo")

    with pytest.raises(InputError, match="Specify at least one cache target"):
        resolve_targets([])
    with pytest.raises(InputError, match="must not be empty"):
        resolve_targets(["temp", "  "])
    with pytest.raises(InputError, match="Unknown cache target: pnpm, browser"):
        resolve_targets(["temp", "pnpm", "browser", "pnpm"])


def test_bad_targets_fail_before_scanning_or_locking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_lock(*_args: object, **_kwargs: object) -> object:
        pytest.fail("target_lock must not be called for invalid targets")

    monkeypatch.setattr(cleanup, "target_lock", fail_lock)
    state = tmp_path / "state"

    with pytest.raises(InputError, match="Specify at least one cache target"):
        clean_caches([], apply=True, roots=_roots(tmp_path), state_root=state)
    with pytest.raises(InputError, match="Unknown cache target: browser"):
        clean_caches(["browser"], apply=True, roots=_roots(tmp_path), state_root=state)
    assert not state.exists()


def test_default_cache_roots_windows_and_posix(tmp_path: Path) -> None:
    local = tmp_path / "local"
    profile = tmp_path / "profile"
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"

    win = default_cache_roots(
        env={"LOCALAPPDATA": str(local), "USERPROFILE": str(profile)},
        platform="win32",
        home=home,
    )
    assert win == {
        "temp": local / "Temp",
        "pip": local / "pip" / "Cache",
        "uv": local / "uv" / "cache",
        "npm": local / "npm-cache",
        "nuget": profile / ".nuget" / "packages",
        "cargo": profile / ".cargo" / "registry",
    }

    win_fallback = default_cache_roots(env={}, platform="win32", home=home)
    assert win_fallback["temp"] == home / "AppData" / "Local" / "Temp"
    assert win_fallback["nuget"] == home / ".nuget" / "packages"

    linux_xdg = default_cache_roots(env={"XDG_CACHE_HOME": str(xdg)}, platform="linux", home=home)
    assert linux_xdg["temp"] == xdg / "tmp"
    assert linux_xdg["pip"] == xdg / "pip"
    assert linux_xdg["npm"] == home / ".npm"

    linux_fallback = default_cache_roots(env={}, platform="linux", home=home)
    assert linux_fallback["temp"] == home / ".cache" / "tmp"
    assert linux_fallback["cargo"] == home / ".cargo" / "registry"


def test_dry_run_reports_planned_paths_and_leaves_files_and_history_untouched(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    state = tmp_path / "state"
    old_temp = _write(roots["temp"] / "old.tmp", b"12345", age_seconds=TEMP_MAX_AGE_SECONDS)
    fresh_temp = _write(
        roots["temp"] / "fresh.tmp", b"999999", age_seconds=TEMP_MAX_AGE_SECONDS - 1
    )
    pip_file = _write(roots["pip"] / "wheels" / "pkg.whl", b"abcdefgh")
    untouched_uv = _write(roots["uv"] / "archive.bin", b"uvdata")

    # Even a held lock must not block dry-run.
    with target_lock(roots["pip"], state_root=state):
        events = list(
            clean_caches(
                ["temp", "pip"],
                roots=roots,
                state_root=state,
                now=_clock,
            )
        )

    assert events == [
        Progress(1, 2, old_temp),
        Progress(2, 2, pip_file),
        CacheCleanResult(
            targets=("temp", "pip"),
            items=(
                CacheItem("temp", old_temp, 5),
                CacheItem("pip", pip_file, 8),
            ),
            total_bytes=13,
            dry_run=True,
            batch=BatchResult(succeeded=2),
        ),
    ]
    assert old_temp.exists()
    assert fresh_temp.exists()
    assert pip_file.exists()
    assert untouched_uv.exists()
    assert not HistoryStore(state_root=state).path.exists()


def test_apply_deletes_eligible_files_keeps_fresh_temp_and_root_dirs(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    state = tmp_path / "state"
    old_temp = _write(roots["temp"] / "old_dir" / "old.tmp", b"1234", age_seconds=90000)
    old_ts = (NOW - timedelta(seconds=90000)).timestamp()
    os.utime(old_temp.parent, (old_ts, old_ts))

    fresh_temp = _write(roots["temp"] / "mixed" / "fresh.tmp", b"new", age_seconds=100)
    mixed_old = _write(roots["temp"] / "mixed" / "stale.tmp", b"olddd", age_seconds=90000)
    fresh_empty = roots["temp"] / "fresh_empty"
    fresh_empty.mkdir()
    fresh_ts = (NOW - timedelta(seconds=100)).timestamp()
    os.utime(fresh_empty, (fresh_ts, fresh_ts))

    old_empty = roots["temp"] / "old_empty"
    old_empty.mkdir()
    os.utime(old_empty, (old_ts, old_ts))

    pip_file = _write(roots["pip"] / "sub" / "a.whl", b"abc")
    pip_empty = roots["pip"] / "empty"
    pip_empty.mkdir()
    uv_file = _write(roots["uv"] / "u.bin", b"u")
    npm_file = _write(roots["npm"] / "n.tgz", b"nn")
    nuget_file = _write(roots["nuget"] / "pkg.nupkg", b"nnn")
    cargo_file = _write(roots["cargo"] / "crate.crate", b"cccc")

    events = list(
        clean_caches(
            CACHE_TARGETS,
            apply=True,
            roots=roots,
            state_root=state,
            now=_clock,
        )
    )
    result = events[-1]
    assert isinstance(result, CacheCleanResult)
    assert result.dry_run is False
    assert result.total_bytes == 4 + 5 + 3 + 1 + 2 + 3 + 4
    assert result.batch == BatchResult(succeeded=7)

    assert not old_temp.exists()
    assert not old_temp.parent.exists()
    assert not old_empty.exists()
    assert fresh_temp.exists()
    assert not mixed_old.exists()
    assert fresh_empty.exists()

    for name in CACHE_TARGETS:
        assert roots[name].is_dir()
    assert not pip_file.exists()
    assert not pip_empty.exists()
    assert not (roots["pip"] / "sub").exists()
    assert not uv_file.exists()
    assert not npm_file.exists()
    assert not nuget_file.exists()
    assert not cargo_file.exists()

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "cache-clean"
    assert runs[0].arguments == {"apply": True, "targets": list(CACHE_TARGETS)}
    assert runs[0].succeeded == 7
    assert runs[0].failed == 0
    assert runs[0].status == "succeeded"


def test_missing_root_and_non_directory_root(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    missing_result = list(clean_caches(["temp", "pip"], roots=roots, now=_clock))[-1]
    assert missing_result == CacheCleanResult(
        targets=("temp", "pip"),
        items=(),
        total_bytes=0,
        dry_run=True,
        batch=BatchResult(succeeded=0),
    )

    roots["pip"].parent.mkdir(parents=True, exist_ok=True)
    roots["pip"].write_text("not a dir", encoding="utf-8")
    file_root_result = list(clean_caches(["pip"], roots=roots, now=_clock))[-1]
    assert isinstance(file_root_result, CacheCleanResult)
    assert file_root_result.batch.succeeded == 0
    assert file_root_result.batch.failures == (
        ItemFailure(roots["pip"], "Path is not a directory."),
    )


def test_links_and_junctions_are_not_followed_or_deleted(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    state = tmp_path / "state"
    outside = _write(tmp_path / "outside" / "keep.txt", b"secret")
    inside = _write(roots["pip"] / "sub" / "cache.whl", b"123")

    junction_path = roots["pip"] / "sub" / "link_dir"
    if sys.platform == "win32":
        _winapi.CreateJunction(str(outside.parent), str(junction_path))
    else:
        junction_path.symlink_to(outside.parent, target_is_directory=True)

    link_root = roots["uv"]
    link_root.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        _winapi.CreateJunction(str(outside.parent), str(link_root))
    else:
        link_root.symlink_to(outside.parent, target_is_directory=True)

    try:
        result = list(
            clean_caches(["pip", "uv"], apply=True, roots=roots, state_root=state, now=_clock)
        )[-1]
        assert isinstance(result, CacheCleanResult)
        assert result.items == (CacheItem("pip", inside, 3),)
        assert not inside.exists()
        assert junction_path.exists() or junction_path.is_junction() or junction_path.is_symlink()
        assert outside.exists()
    finally:
        if junction_path.is_junction():
            junction_path.rmdir()
        elif junction_path.is_symlink():
            junction_path.unlink()
        if link_root.is_junction():
            link_root.rmdir()
        elif link_root.is_symlink():
            link_root.unlink()


def test_symlink_file_is_not_deleted_when_supported(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    outside = _write(tmp_path / "outside" / "target.txt", b"keep")
    roots["pip"].mkdir(parents=True, exist_ok=True)
    link_file = roots["pip"] / "link.txt"
    try:
        link_file.symlink_to(outside)
    except OSError:
        pytest.skip("Creating a symbolic link needs a privilege on this machine.")

    result = list(
        clean_caches(["pip"], apply=True, roots=roots, state_root=tmp_path / "state", now=_clock)
    )[-1]
    assert isinstance(result, CacheCleanResult)
    assert result.batch.succeeded == 0
    assert link_file.is_symlink()
    assert outside.exists()


def test_in_use_and_unreadable_entries_record_item_failures_and_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _roots(tmp_path)
    state = tmp_path / "state"
    locked_file = _write(roots["pip"] / "locked.whl", b"1111")
    ok_file = _write(roots["pip"] / "ok.whl", b"22")
    blocked_dir = roots["pip"] / "blocked_dir"
    blocked_dir.mkdir(parents=True)

    real_unlink = Path.unlink
    real_scandir = cleanup.os.scandir

    def fake_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == locked_file:
            err = PermissionError(errno.EACCES, "localized in use")
            err.winerror = 32
            raise err
        real_unlink(self, missing_ok=missing_ok)

    def fake_scandir(path: str | cleanup.os.PathLike[str]) -> object:
        if Path(path) == blocked_dir:
            raise PermissionError(errno.EACCES, "localized denied")
        return real_scandir(path)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    monkeypatch.setattr(cleanup.os, "scandir", fake_scandir)

    result = list(clean_caches(["pip"], apply=True, roots=roots, state_root=state, now=_clock))[-1]
    assert isinstance(result, CacheCleanResult)
    assert result.items == (CacheItem("pip", ok_file, 2),)
    assert result.total_bytes == 2
    assert result.batch.succeeded == 1
    assert not ok_file.exists()
    assert locked_file.exists()

    assert result.batch.failures == (
        ItemFailure(blocked_dir, "access is denied"),
        ItemFailure(locked_file, "file is in use by another process"),
    )

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].succeeded == 1
    assert runs[0].failed == 2
    assert runs[0].status == "partial"


def test_cancel_during_delete_and_during_scan_and_generator_close(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    state = tmp_path / "state"
    first = _write(roots["pip"] / "a.whl", b"aa")
    second = _write(roots["pip"] / "b.whl", b"bb")

    done = 0

    def cancel_after_one() -> bool:
        return done >= 1

    events: list[Progress | CacheCleanResult] = []
    for event in clean_caches(
        ["pip"], apply=True, roots=roots, state_root=state, cancel=cancel_after_one, now=_clock
    ):
        events.append(event)
        if isinstance(event, Progress):
            done += 1

    last = events[-1]
    assert isinstance(last, CacheCleanResult)
    assert last.batch == BatchResult(succeeded=1, cancelled=True)
    assert not first.exists()
    assert second.exists()

    runs = HistoryStore(state_root=state).list_runs()
    assert runs[0].cancelled is True
    assert runs[0].succeeded == 1

    # Cancel during scanning phase before items are iterated.
    _write(roots["uv"] / "c.bin", b"cc")
    scan_cancelled = list(clean_caches(["uv"], roots=roots, cancel=lambda: True, now=_clock))[-1]
    assert scan_cancelled == CacheCleanResult(
        targets=("uv",),
        items=(),
        total_bytes=0,
        dry_run=True,
        batch=BatchResult(succeeded=0, cancelled=True),
    )

    # Closing generator early records cancelled=True in history.
    _write(roots["npm"] / "1.tgz", b"1")
    _write(roots["npm"] / "2.tgz", b"2")
    gen = cast(
        "Generator[Progress | CacheCleanResult, None, None]",
        clean_caches(["npm"], apply=True, roots=roots, state_root=state, now=_clock),
    )
    assert isinstance(next(gen), Progress)
    gen.close()
    assert HistoryStore(state_root=state).list_runs()[0].cancelled is True


def test_lock_conflict_raises_operation_error_and_records_history(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    state = tmp_path / "state"
    target_file = _write(roots["pip"] / "a.whl", b"123")

    with (
        target_lock(roots["pip"], state_root=state),
        pytest.raises(OperationError, match="Target is locked by another run"),
    ):
        list(clean_caches(["pip"], apply=True, roots=roots, state_root=state, now=_clock))

    assert target_file.exists()
    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "Target is locked by another run" in runs[0].error


def test_partial_roots_override_uses_injected_env_for_missing_keys(tmp_path: Path) -> None:
    custom_temp = tmp_path / "custom_temp"
    _write(custom_temp / "old.tmp", b"12", age_seconds=90000)
    local = tmp_path / "local"
    pip_file = _write(local / "pip" / "Cache" / "a.whl", b"123")

    result = list(
        clean_caches(
            ["temp", "pip"],
            roots={"temp": custom_temp},
            env={"LOCALAPPDATA": str(local)},
            home=tmp_path / "home",
            now=_clock,
        )
    )[-1]
    assert isinstance(result, CacheCleanResult)
    assert [item.path for item in result.items] == [custom_temp / "old.tmp", pip_file]


def test_os_error_reason_handles_known_and_fallback_errors() -> None:
    assert cleanup._os_error_reason(OSError(errno.ENOENT, "x")) == "path was not found"
    assert (
        cleanup._os_error_reason(OSError(errno.ENOTDIR, "x")) == "a parent path is not a directory"
    )
    assert cleanup._os_error_reason(OSError(errno.EROFS, "x")) == "the file system is read-only"
    assert cleanup._os_error_reason(OSError(errno.ENOLCK, "no locks left")) == "no locks left"
