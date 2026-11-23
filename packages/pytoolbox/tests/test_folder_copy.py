import errno
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
from pytoolbox.tools.system import folder_copy
from pytoolbox.tools.system.folder_copy import (
    DEFAULT_FORMAT,
    FolderCopyResult,
    copy_folder_batch,
)


def _seed_source(root: Path, name: str = "template") -> Path:
    src = root / name
    (src / "sub" / "nested").mkdir(parents=True)
    (src / "empty").mkdir()
    (src / "readme.txt").write_text("hello", encoding="utf-8")
    (src / "sub" / "nested" / "data.bin").write_bytes(b"\x00\x01\x02")
    return src


def test_copy_folder_batch_copies_trees_streams_progress_and_records_history(
    tmp_path: Path,
) -> None:
    src = _seed_source(tmp_path)
    dest = tmp_path / "out"
    state = tmp_path / "state"

    events = list(copy_folder_batch(src, dest, 3, state_root=state))

    expected_targets = (
        (dest / "template_001").resolve(),
        (dest / "template_002").resolve(),
        (dest / "template_003").resolve(),
    )
    assert events == [
        Progress(1, 3, expected_targets[0]),
        Progress(2, 3, expected_targets[1]),
        Progress(3, 3, expected_targets[2]),
        FolderCopyResult(
            source=src.resolve(),
            dest_dir=dest.resolve(),
            items=expected_targets,
            dry_run=False,
            batch=BatchResult(succeeded=3),
        ),
    ]
    for target in expected_targets:
        assert (target / "readme.txt").read_text(encoding="utf-8") == "hello"
        assert (target / "sub" / "nested" / "data.bin").read_bytes() == b"\x00\x01\x02"
        assert (target / "empty").is_dir()

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "folder-copy"
    assert runs[0].status == "succeeded"
    assert runs[0].succeeded == 3
    assert runs[0].failed == 0
    assert runs[0].arguments == {
        "source": str(src.resolve()),
        "dest_dir": str(dest.resolve()),
        "count": 3,
        "format": DEFAULT_FORMAT,
        "start": 1,
        "overwrite": False,
    }


def test_custom_format_and_start_number_with_subdirectories(tmp_path: Path) -> None:
    src = _seed_source(tmp_path, "app")
    dest = tmp_path / "copies"
    state = tmp_path / "state"

    result = list(
        copy_folder_batch(src, dest, 2, fmt="batch/{name}-{n:02d}", start=10, state_root=state)
    )[-1]

    assert isinstance(result, FolderCopyResult)
    assert result.items == (
        (dest / "batch" / "app-10").resolve(),
        (dest / "batch" / "app-11").resolve(),
    )
    assert (dest / "batch" / "app-10" / "readme.txt").read_text(encoding="utf-8") == "hello"
    assert (
        dest / "batch" / "app-11" / "sub" / "nested" / "data.bin"
    ).read_bytes() == b"\x00\x01\x02"


def test_dry_run_plans_without_writing_locking_or_recording_history(tmp_path: Path) -> None:
    src = _seed_source(tmp_path)
    dest = tmp_path / "planned_dest"
    state = tmp_path / "state"
    target_1 = (dest / "template_001").resolve()

    with target_lock(target_1, state_root=state):
        events = list(copy_folder_batch(src, dest, 2, dry_run=True, state_root=state))

    assert events[-1] == FolderCopyResult(
        source=src.resolve(),
        dest_dir=dest.resolve(),
        items=(target_1, (dest / "template_002").resolve()),
        dry_run=True,
        batch=BatchResult(succeeded=2),
    )
    assert not dest.exists()
    assert not HistoryStore(state_root=state).path.exists()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"count": 0}, "Count must be at least 1"),
        ({"count": -2}, "Count must be at least 1"),
        ({"count": True}, "Count must be at least 1"),
        ({"start": False}, "Start number must be an integer"),
        ({"fmt": "   "}, "Folder name format must not be empty"),
        ({"fmt": "{unknown}_{n}"}, "Invalid folder name format"),
        ({"fmt": "{}_{n}"}, "Invalid folder name format"),
        ({"fmt": "{n:invalid}"}, "Invalid folder name format"),
        ({"fmt": "   \t  "}, "Folder name format must not be empty"),
        ({"fmt": "../escape_{n}"}, "must be a relative path"),
        ({"fmt": "/abs/{n}"}, "must be a relative path"),
        ({"fmt": "."}, "escapes destination directory"),
        ({"fmt": "{name}", "count": 2}, "Duplicate target folder path"),
    ],
)
def test_validation_rejects_bad_arguments_upfront(
    tmp_path: Path, kwargs: dict[str, object], message: str
) -> None:
    src = _seed_source(tmp_path)
    dest = tmp_path / "dest"
    params: dict[str, object] = {"count": 1}
    params.update(kwargs)

    with pytest.raises(InputError, match=message):
        copy_folder_batch(
            src,
            dest,
            cast("int", params["count"]),
            fmt=cast("str", params.get("fmt", DEFAULT_FORMAT)),
            start=cast("int", params.get("start", 1)),
        )


def test_validation_rejects_empty_paths_missing_source_and_bad_destinations(
    tmp_path: Path,
) -> None:
    src = _seed_source(tmp_path)
    dest = tmp_path / "dest"
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(InputError, match="Source folder path must not be empty"):
        copy_folder_batch(Path("  "), dest, 1)
    with pytest.raises(InputError, match="Destination directory path must not be empty"):
        copy_folder_batch(src, Path("  "), 1)
    with pytest.raises(InputError, match="Source folder does not exist"):
        copy_folder_batch(tmp_path / "missing", dest, 1)
    with pytest.raises(InputError, match="Source folder does not exist"):
        copy_folder_batch(file_path, dest, 1)
    with pytest.raises(InputError, match="Destination path is not a directory"):
        copy_folder_batch(src, file_path, 1)
    with pytest.raises(InputError, match="cannot be the source folder or inside it"):
        copy_folder_batch(src, src, 1)
    with pytest.raises(InputError, match="cannot be the source folder or inside it"):
        copy_folder_batch(src, src / "inner", 1)


def test_validation_rejects_target_matching_or_containing_source_and_existing_targets(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "work"
    sub = parent / "group"
    sub.mkdir(parents=True)
    src = _seed_source(sub, "src")

    with pytest.raises(InputError, match="cannot match or contain source folder"):
        copy_folder_batch(src, sub, 1, fmt="{name}")
    with pytest.raises(InputError, match="cannot match or contain source folder"):
        copy_folder_batch(src, parent, 1, fmt="group")

    dest = tmp_path / "dest"
    (dest / "src_001").mkdir(parents=True)
    with pytest.raises(InputError, match="Target already exists"):
        copy_folder_batch(src, dest, 2)


def test_validation_rejects_empty_rendered_name(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    src = _seed_source(tmp_path, "src")
    with pytest.raises(InputError, match="produced an empty target name"):
        copy_folder_batch(src, dest, 1, fmt="{name:.0}")


def test_links_and_junctions_in_source_dest_and_inside_source_are_handled_safely(
    tmp_path: Path,
) -> None:
    outside = _seed_source(tmp_path, "outside")
    src = _seed_source(tmp_path, "src")
    dest = tmp_path / "dest"
    state = tmp_path / "state"

    src_junction = src / "link_dir"
    link_dest = tmp_path / "link_dest"
    if sys.platform == "win32":
        _winapi.CreateJunction(str(outside), str(src_junction))
        _winapi.CreateJunction(str(outside), str(link_dest))
    else:
        src_junction.symlink_to(outside, target_is_directory=True)
        link_dest.symlink_to(outside, target_is_directory=True)

    try:
        with pytest.raises(InputError, match="Links and junctions are not followed"):
            copy_folder_batch(link_dest, dest, 1)
        with pytest.raises(InputError, match="Links and junctions are not followed"):
            copy_folder_batch(src, link_dest, 1)

        dest.mkdir()
        target_link = dest / "src_001"
        if sys.platform == "win32":
            _winapi.CreateJunction(str(outside), str(target_link))
        else:
            target_link.symlink_to(outside, target_is_directory=True)
        try:
            with pytest.raises(InputError, match="Links and junctions are not followed"):
                copy_folder_batch(src, dest, 1, overwrite=True)
        finally:
            if target_link.is_junction():
                target_link.rmdir()
            elif target_link.is_symlink():
                target_link.unlink()

        result = list(copy_folder_batch(src, dest, 1, state_root=state))[-1]
        assert isinstance(result, FolderCopyResult)
        copied = dest / "src_001"
        assert (copied / "readme.txt").is_file()
        assert not (copied / "link_dir").exists()
        assert (outside / "readme.txt").exists()
    finally:
        if src_junction.is_junction():
            src_junction.rmdir()
        elif src_junction.is_symlink():
            src_junction.unlink()
        if link_dest.is_junction():
            link_dest.rmdir()
        elif link_dest.is_symlink():
            link_dest.unlink()


def test_overwrite_replaces_existing_files_directories_and_links_without_touching_targets(
    tmp_path: Path,
) -> None:
    src = _seed_source(tmp_path, "src")
    outside = _seed_source(tmp_path, "outside")
    dest = tmp_path / "dest"
    dest.mkdir()
    state = tmp_path / "state"

    existing_dir = dest / "src_001"
    (existing_dir / "old_sub").mkdir(parents=True)
    (existing_dir / "old_sub" / "stale.txt").write_text("stale", encoding="utf-8")
    nested_link = existing_dir / "old_sub" / "junc"
    if sys.platform == "win32":
        _winapi.CreateJunction(str(outside), str(nested_link))
    else:
        nested_link.symlink_to(outside, target_is_directory=True)

    existing_file = dest / "src_002"
    existing_file.write_text("not a dir", encoding="utf-8")

    try:
        result = list(copy_folder_batch(src, dest, 2, overwrite=True, state_root=state))[-1]
        assert isinstance(result, FolderCopyResult)
        assert result.batch == BatchResult(succeeded=2)
        assert not (existing_dir / "old_sub").exists()
        assert (existing_dir / "readme.txt").read_text(encoding="utf-8") == "hello"
        assert existing_file.is_dir()
        assert (existing_file / "readme.txt").read_text(encoding="utf-8") == "hello"
        assert (outside / "readme.txt").read_text(encoding="utf-8") == "hello"
    finally:
        if nested_link.is_junction():
            nested_link.rmdir()
        elif nested_link.is_symlink():
            nested_link.unlink()


def test_remove_link_handles_symlink_files_and_dirs(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating a symbolic link needs a privilege on this machine.")

    folder_copy._remove_existing_target(link)
    assert not link.exists()
    assert not link.is_symlink()
    assert outside.exists()


def test_remove_link_fallback_to_rmdir_when_unlink_raises_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    target = tmp_path / "dir_link"
    target.mkdir()

    def fake_unlink(self: Path, missing_ok: bool = False) -> None:
        del self, missing_ok
        raise PermissionError(errno.EACCES, "cannot unlink directory symlink")

    real_rmdir = Path.rmdir

    def fake_rmdir(self: Path) -> None:
        calls.append(self.name)
        real_rmdir(self)

    def not_junction(_self: Path) -> bool:
        return False

    monkeypatch.setattr(Path, "is_junction", not_junction)
    monkeypatch.setattr(Path, "unlink", fake_unlink)
    monkeypatch.setattr(Path, "rmdir", fake_rmdir)

    folder_copy._remove_link(target)
    assert calls == ["dir_link"]
    assert not target.exists()


def test_os_error_per_target_records_item_failure_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _seed_source(tmp_path, "src")
    dest = tmp_path / "dest"
    state = tmp_path / "state"
    target_1 = (dest / "src_001").resolve()
    target_2 = (dest / "src_002").resolve()

    real_copy2 = folder_copy.shutil.copy2

    def flaky_copy2(src_path: Path, dst_path: Path, *, follow_symlinks: bool = True) -> object:
        if dst_path.is_relative_to(target_1):
            raise PermissionError(errno.EACCES, "localized denied")
        return real_copy2(src_path, dst_path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(folder_copy.shutil, "copy2", flaky_copy2)

    events = list(copy_folder_batch(src, dest, 2, state_root=state))
    assert events[:2] == [Progress(1, 2, target_1), Progress(2, 2, target_2)]
    result = events[-1]
    assert result == FolderCopyResult(
        source=src.resolve(),
        dest_dir=dest.resolve(),
        items=(target_2,),
        dry_run=False,
        batch=BatchResult(
            succeeded=1,
            failures=(ItemFailure(target_1, "access is denied"),),
            cancelled=False,
        ),
    )
    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "partial"
    assert runs[0].succeeded == 1
    assert runs[0].failed == 1


def test_dest_dir_creation_failure_raises_operation_error_and_records_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _seed_source(tmp_path, "src")
    dest = tmp_path / "blocked_dest"
    state = tmp_path / "state"

    real_mkdir = Path.mkdir

    def fail_mkdir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if self == dest.resolve(strict=False):
            raise PermissionError(errno.EACCES, "localized denied")
        real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    with pytest.raises(OperationError, match="Cannot create destination directory"):
        list(copy_folder_batch(src, dest, 1, state_root=state))

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "access is denied" in runs[0].error


def test_lock_conflict_raises_operation_error_and_records_history(tmp_path: Path) -> None:
    src = _seed_source(tmp_path, "src")
    dest = tmp_path / "dest"
    state = tmp_path / "state"
    target_2 = (dest / "src_002").resolve()

    with (
        target_lock(target_2, state_root=state),
        pytest.raises(OperationError, match="Target is locked by another run"),
    ):
        list(copy_folder_batch(src, dest, 2, state_root=state))

    assert not (dest / "src_001").exists()
    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "Target is locked by another run" in runs[0].error


def test_target_appearing_before_lock_raises_input_error(tmp_path: Path) -> None:
    src = _seed_source(tmp_path, "src")
    dest = tmp_path / "dest"
    state = tmp_path / "state"

    gen = copy_folder_batch(src, dest, 1, state_root=state)
    (dest / "src_001").mkdir(parents=True)

    with pytest.raises(InputError, match="Target already exists"):
        list(gen)

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"


def test_cancel_between_targets_and_generator_close_record_history(tmp_path: Path) -> None:
    src = _seed_source(tmp_path, "src")
    dest = tmp_path / "dest"
    state = tmp_path / "state"

    done = 0

    def cancel_after_one() -> bool:
        return done >= 1

    events: list[Progress | FolderCopyResult] = []
    for event in copy_folder_batch(src, dest, 3, state_root=state, cancel=cancel_after_one):
        events.append(event)
        if isinstance(event, Progress):
            done += 1

    last = events[-1]
    assert isinstance(last, FolderCopyResult)
    assert last.batch == BatchResult(succeeded=1, cancelled=True)
    assert (dest / "src_001").is_dir()
    assert not (dest / "src_002").exists()

    runs = HistoryStore(state_root=state).list_runs()
    assert runs[0].cancelled is True
    assert runs[0].succeeded == 1

    dest2 = tmp_path / "dest2"
    gen = cast(
        "Generator[Progress | FolderCopyResult, None, None]",
        copy_folder_batch(src, dest2, 2, state_root=state),
    )
    assert isinstance(next(gen), Progress)
    gen.close()
    assert HistoryStore(state_root=state).list_runs()[0].cancelled is True


def test_os_error_reason_maps_known_and_fallback_errors() -> None:
    class _LockedError(OSError):
        winerror: int = 32

    assert (
        folder_copy._os_error_reason(_LockedError(errno.EACCES, "x"))
        == "file is in use by another process"
    )
    assert folder_copy._os_error_reason(OSError(errno.ENOSPC, "x")) == "no space left on device"
    assert folder_copy._os_error_reason(OSError(errno.ENOLCK, "lock limit")) == "lock limit"


def test_dot_source_uses_resolved_directory_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _seed_source(tmp_path, "current_proj")
    dest = tmp_path / "dest"
    state = tmp_path / "state"
    monkeypatch.chdir(src)

    result = list(copy_folder_batch(Path(), dest, 1, dry_run=True, state_root=state))[-1]
    assert isinstance(result, FolderCopyResult)
    assert result.items == ((dest / "current_proj_001").resolve(),)
