import errno
import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import cast

if sys.platform == "win32":
    import _winapi

import pytest

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, ItemFailure, Progress
from pytoolbox.tools.system import file_copy
from pytoolbox.tools.system.file_copy import DEFAULT_FORMAT, FileCopyResult, copy_file_batch


def _make_source(path: Path, content: bytes = b"hello world\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_copy_file_batch_copies_with_default_format_and_records_history(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "report.txt", b"payload-123")
    old_ts = 1_700_000_000.0
    os.utime(src, (old_ts, old_ts))
    dest = tmp_path / "nested" / "out"
    state = tmp_path / "state"

    events = list(copy_file_batch(src, dest, 3, state_root=state))

    expected_targets = (
        dest.resolve() / "report_001.txt",
        dest.resolve() / "report_002.txt",
        dest.resolve() / "report_003.txt",
    )
    assert events[:-1] == [
        Progress(1, 3, expected_targets[0]),
        Progress(2, 3, expected_targets[1]),
        Progress(3, 3, expected_targets[2]),
    ]
    result = events[-1]
    assert result == FileCopyResult(
        source=src.resolve(),
        dest_dir=dest.resolve(),
        items=expected_targets,
        dry_run=False,
        batch=BatchResult(succeeded=3),
    )
    for target in expected_targets:
        assert target.read_bytes() == b"payload-123"
        assert abs(target.stat().st_mtime - old_ts) < 2.0

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "file-copy"
    assert runs[0].arguments == {
        "source": str(src.resolve()),
        "dest_dir": str(dest.resolve()),
        "count": 3,
        "format": DEFAULT_FORMAT,
        "start": 1,
        "overwrite": False,
    }
    assert runs[0].succeeded == 3
    assert runs[0].failed == 0
    assert runs[0].status == "succeeded"


def test_copy_file_batch_supports_custom_format_start_subdirs_and_no_suffix(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path / "README", b"readme")
    dest = tmp_path / "out"
    state = tmp_path / "state"

    events = list(
        copy_file_batch(
            src,
            dest,
            2,
            fmt="batch_{n}/{name}_{n:02d}{suffix}",
            start=0,
            state_root=state,
        )
    )
    result = events[-1]
    assert isinstance(result, FileCopyResult)
    assert result.items == (
        dest.resolve() / "batch_0" / "README_00",
        dest.resolve() / "batch_1" / "README_01",
    )
    assert (dest / "batch_0" / "README_00").read_bytes() == b"readme"
    assert (dest / "batch_1" / "README_01").read_bytes() == b"readme"


def test_dry_run_plans_targets_without_writing_locking_or_recording_history(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path / "data.bin", b"abc")
    dest = tmp_path / "missing_dir"
    state = tmp_path / "state"
    planned = dest.resolve() / "data_005.bin"

    # Held lock must not block dry-run.
    with target_lock(planned, state_root=state):
        events = list(copy_file_batch(src, dest, 1, start=5, dry_run=True, state_root=state))

    assert events == [
        Progress(1, 1, planned),
        FileCopyResult(
            source=src.resolve(),
            dest_dir=dest.resolve(),
            items=(planned,),
            dry_run=True,
            batch=BatchResult(succeeded=1),
        ),
    ]
    assert not dest.exists()
    assert not HistoryStore(state_root=state).path.exists()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"count": 0}, "Count must be at least 1"),
        ({"count": -3}, "Count must be at least 1"),
        ({"fmt": "   "}, "Format must not be empty"),
        ({"fmt": "{missing}"}, "Invalid format"),
        ({"fmt": "{}"}, "Invalid format"),
        ({"fmt": "{n:zzz}"}, "Invalid format"),
        ({"fmt": "{stem.missing}"}, "Invalid format"),
        ({"fmt": "{suffix}"}, "must not be empty"),
        ({"fmt": "sub/{n}/"}, "must name a file"),
        ({"fmt": "/abs_{n}.txt"}, "must stay inside destination folder"),
        ({"fmt": "../escape_{n}.txt"}, "escapes destination folder"),
        ({"fmt": "sub/../../escape_{n}.txt"}, "escapes destination folder"),
        ({"fmt": "."}, "escapes destination folder"),
        ({"fmt": "sub/."}, "must name a file"),
        ({"fmt": "./sub/../{name}", "count": 2}, "Duplicate destination path"),
    ],
)
def test_upfront_validation_rejects_bad_count_or_format_synchronously(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
    message: str,
) -> None:
    def fail_lock(*_args: object, **_kwargs: object) -> object:
        pytest.fail("target_lock must not be called on invalid inputs")

    monkeypatch.setattr(file_copy, "target_lock", fail_lock)
    src = _make_source(tmp_path / "note")
    dest = tmp_path / "dest"
    state = tmp_path / "state"
    count = cast("int", kwargs.pop("count", 1))
    fmt = cast("str", kwargs.pop("fmt", DEFAULT_FORMAT))

    with pytest.raises(InputError, match=message):
        copy_file_batch(src, dest, count, fmt=fmt, state_root=state)
    assert not state.exists()


def test_upfront_validation_rejects_bad_source_dest_self_overwrite_and_existing_targets(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(InputError, match="Source file was not found"):
        copy_file_batch(tmp_path / "missing.txt", dest, 1)

    with pytest.raises(InputError, match="Source path is not a regular file"):
        copy_file_batch(dest, tmp_path / "out", 1)

    with pytest.raises(InputError, match="Source path must not be empty"):
        copy_file_batch(Path("   "), dest, 1)

    src = _make_source(tmp_path / "note.txt", b"original")
    with pytest.raises(InputError, match="Destination folder must not be empty"):
        copy_file_batch(src, Path("   "), 1)

    dest_file = _make_source(tmp_path / "not_a_dir.txt", b"x")
    with pytest.raises(InputError, match="Destination path is not a directory"):
        copy_file_batch(src, dest_file, 1)

    # Cannot target source itself even with overwrite=True.
    with pytest.raises(InputError, match="cannot overwrite source file"):
        copy_file_batch(src, tmp_path, 1, fmt="{name}", overwrite=True)

    # Cannot overwrite existing destination without overwrite=True.
    existing = _make_source(dest / "note_001.txt", b"old")
    with pytest.raises(InputError, match="Destination already exists"):
        copy_file_batch(src, dest, 1)
    assert existing.read_bytes() == b"old"

    # Windows drive-relative path in format.
    if sys.platform == "win32":
        with pytest.raises(InputError, match="must stay inside destination folder"):
            copy_file_batch(src, dest, 1, fmt="C:drive_{n}.txt")


def test_overwrite_replaces_existing_regular_files(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "a.txt", b"new-content")
    dest = tmp_path / "dest"
    dest.mkdir()
    existing = _make_source(dest / "a_001.txt", b"old-content")
    state = tmp_path / "state"

    result = list(copy_file_batch(src, dest, 2, overwrite=True, state_root=state))[-1]
    assert isinstance(result, FileCopyResult)
    assert result.batch == BatchResult(succeeded=2)
    assert existing.read_bytes() == b"new-content"
    assert (dest / "a_002.txt").read_bytes() == b"new-content"


def test_links_and_junctions_are_rejected_and_not_followed(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_file = _make_source(real_dir / "item.txt", b"real")
    dest = tmp_path / "dest"
    dest.mkdir()

    junction_src = tmp_path / "junc_src"
    junction_dest = tmp_path / "junc_dest"
    junction_sub = dest / "junc_sub"
    if sys.platform == "win32":
        _winapi.CreateJunction(str(real_dir), str(junction_src))
        _winapi.CreateJunction(str(dest), str(junction_dest))
        _winapi.CreateJunction(str(real_dir), str(junction_sub))
    else:
        junction_src.symlink_to(real_dir, target_is_directory=True)
        junction_dest.symlink_to(dest, target_is_directory=True)
        junction_sub.symlink_to(real_dir, target_is_directory=True)

    try:
        with pytest.raises(InputError, match="Links and junctions are not followed"):
            copy_file_batch(junction_src / "item.txt", dest, 1)
        with pytest.raises(InputError, match="Links and junctions are not followed"):
            copy_file_batch(real_file, junction_dest / "sub", 1)
        with pytest.raises(InputError, match="Links and junctions are not followed"):
            copy_file_batch(real_file, dest, 1, fmt="junc_sub/{stem}_{n}{suffix}")
    finally:
        for link in (junction_src, junction_dest, junction_sub):
            if link.is_junction():
                link.rmdir()
            elif link.is_symlink():
                link.unlink()


def test_symlinks_on_source_and_targets_are_refused(tmp_path: Path) -> None:
    real = _make_source(tmp_path / "real.txt", b"real")
    outside = _make_source(tmp_path / "outside.txt", b"keep-me")
    dest = tmp_path / "dest"
    dest.mkdir()
    state = tmp_path / "state"

    victim = _make_source(dest / "victim.txt", b"victim-keep")
    sym_src = tmp_path / "sym_src.txt"
    broken_target = dest / "real_001.txt"
    live_target = dest / "real_002.txt"
    inside_target = dest / "real_003.txt"
    try:
        sym_src.symlink_to(real)
        broken_target.symlink_to(tmp_path / "does_not_exist.txt")
        live_target.symlink_to(outside)
        inside_target.symlink_to(victim)
    except OSError:
        pytest.skip("Creating symbolic links needs privilege on this machine.")

    # Source symlink rejected upfront.
    with pytest.raises(InputError, match="Links and junctions are not followed"):
        copy_file_batch(sym_src, dest, 1)

    # Broken symlink at destination rejected when overwrite=False.
    with pytest.raises(InputError, match="Destination already exists"):
        copy_file_batch(real, dest, 1, overwrite=False)

    # With overwrite=True, symlink targets are not followed; recorded as ItemFailure.
    result = list(copy_file_batch(real, dest, 4, overwrite=True, state_root=state))[-1]
    assert isinstance(result, FileCopyResult)
    assert result.items == (dest.resolve() / "real_004.txt",)
    assert result.batch.succeeded == 1
    assert result.batch.failures == (
        ItemFailure(dest.resolve() / "real_001.txt", "Links and junctions are not followed."),
        ItemFailure(dest.resolve() / "real_002.txt", "Links and junctions are not followed."),
        ItemFailure(dest.resolve() / "real_003.txt", "Links and junctions are not followed."),
    )
    assert outside.read_bytes() == b"keep-me"
    assert victim.read_bytes() == b"victim-keep"


def test_directory_at_target_and_copy_os_error_record_item_failures_and_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _make_source(tmp_path / "src.txt", b"data")
    dest = tmp_path / "dest"
    dest.mkdir()
    state = tmp_path / "state"

    dir_target = dest / "src_001.txt"
    dir_target.mkdir()
    locked_target = dest / "src_002.txt"
    ok_target = dest / "src_003.txt"

    real_copy2 = file_copy.shutil.copy2

    class _LockedPermissionError(PermissionError):
        winerror: int = 32

    def fake_copy2(source: Path, dst: Path) -> Path | str:
        if Path(dst) == locked_target:
            raise _LockedPermissionError(errno.EACCES, "localized in use")
        return real_copy2(source, dst)

    monkeypatch.setattr(file_copy.shutil, "copy2", fake_copy2)

    result = list(copy_file_batch(src, dest, 3, overwrite=True, state_root=state))[-1]
    assert isinstance(result, FileCopyResult)
    assert result.items == (ok_target.resolve(),)
    assert result.batch == BatchResult(
        succeeded=1,
        failures=(
            ItemFailure(dir_target.resolve(), "the path is a directory"),
            ItemFailure(locked_target.resolve(), "file is in use by another process"),
        ),
    )
    assert ok_target.read_bytes() == b"data"

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "partial"
    assert runs[0].succeeded == 1
    assert runs[0].failed == 2


def test_mkdir_failure_and_lock_conflict_raise_operation_error_and_record_history(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path / "src.txt", b"abc")
    state = tmp_path / "state"

    blocker = _make_source(tmp_path / "blocker", b"x")
    bad_dest = blocker / "child"
    with pytest.raises(OperationError, match="Cannot create destination folder"):
        list(copy_file_batch(src, bad_dest, 1, state_root=state))

    dest = tmp_path / "dest"
    dest.mkdir()
    locked = dest.resolve() / "src_002.txt"
    with (
        target_lock(locked, state_root=state),
        pytest.raises(OperationError, match="Target is locked by another run"),
    ):
        list(copy_file_batch(src, dest, 2, state_root=state))

    assert not (dest / "src_001.txt").exists()
    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 2
    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "Target is locked by another run" in runs[0].error
    assert runs[1].status == "failed"
    assert runs[1].error is not None
    assert "Cannot create destination folder" in runs[1].error


def test_cancel_between_items_and_early_generator_close_record_history(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "item.txt", b"123")
    dest = tmp_path / "dest"
    state = tmp_path / "state"
    done = 0

    def cancel_after_one() -> bool:
        return done >= 1

    events: list[Progress | FileCopyResult] = []
    for event in copy_file_batch(src, dest, 3, state_root=state, cancel=cancel_after_one):
        events.append(event)
        if isinstance(event, Progress):
            done += 1

    result = events[-1]
    assert result == FileCopyResult(
        source=src.resolve(),
        dest_dir=dest.resolve(),
        items=(dest.resolve() / "item_001.txt",),
        dry_run=False,
        batch=BatchResult(succeeded=1, cancelled=True),
    )
    assert (dest / "item_001.txt").exists()
    assert not (dest / "item_002.txt").exists()

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].cancelled is True
    assert runs[0].status == "cancelled"

    dest2 = tmp_path / "dest2"
    gen = cast(
        "Generator[Progress | FileCopyResult, None, None]",
        copy_file_batch(src, dest2, 3, state_root=state),
    )
    assert isinstance(next(gen), Progress)
    gen.close()

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 2
    assert runs[0].cancelled is True


def test_os_error_reason_handles_known_and_fallback_errors() -> None:
    assert file_copy._os_error_reason(OSError(errno.ENOENT, "x")) == "path was not found"
    assert file_copy._os_error_reason(OSError(errno.EEXIST, "x")) == "the path already exists"
    assert file_copy._os_error_reason(OSError(errno.ENOLCK, "no locks left")) == "no locks left"
