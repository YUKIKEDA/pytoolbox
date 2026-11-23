import errno
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError
from pytoolbox.locks import target_lock
from pytoolbox.results import ItemFailure, Progress
from pytoolbox.tools.system import file_merge
from pytoolbox.tools.system.file_merge import (
    DEFAULT_SEPARATOR,
    FileMergeResult,
    merge_files,
)


def _fixed_now() -> datetime:
    return datetime(2026, 11, 23, 12, 34, 56, tzinfo=UTC)


def _collect(
    values: Iterator[Progress | FileMergeResult],
) -> tuple[list[Progress], FileMergeResult]:
    progress: list[Progress] = []
    result: FileMergeResult | None = None
    for value in values:
        if isinstance(value, Progress):
            progress.append(value)
        else:
            result = value
    assert result is not None
    return progress, result


def test_merge_files_utf8_bom_cp932_and_custom_separator(tmp_path: Path) -> None:
    state = tmp_path / "state"
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"

    first = src_dir / "first.txt"
    first.write_bytes(b"\xef\xbb\xbfalpha\r\nbeta\r\n")
    second = src_dir / "second.txt"
    second.write_bytes("日本語テキスト\r\n".encode("cp932"))

    progress, result = _collect(
        merge_files(
            [first, second],
            out_dir,
            separator="=== {index}:{stem} ({name}) ===",
            state_root=state,
            now=_fixed_now,
        )
    )

    stamp = _fixed_now().astimezone().strftime("%Y%m%d_%H%M%S")
    expected_out = out_dir.resolve() / f"merged_{stamp}.txt"
    assert len(progress) == 2
    assert progress[0] == Progress(1, 2, first.resolve())
    assert progress[1] == Progress(2, 2, second.resolve())
    assert result.output == expected_out
    assert not result.dry_run
    assert result.batch.succeeded == 2
    assert result.batch.failures == ()
    assert not result.batch.cancelled

    raw = expected_out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    assert raw.decode("utf-8") == (
        "=== 1:first (first.txt) ===\nalpha\nbeta\n\n"
        "=== 2:second (second.txt) ===\n日本語テキスト\n"
    )

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "file-merge"
    assert runs[0].succeeded == 2
    assert runs[0].failed == 0


def test_merge_files_records_unreadable_inputs_inline_and_in_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out_dir = tmp_path / "out"

    good = src_dir / "good.txt"
    good.write_text("ok line\n", encoding="utf-8")
    missing = src_dir / "missing.txt"
    folder = src_dir / "subfolder"
    folder.mkdir()
    bad_bytes = src_dir / "binary.bin"
    bad_bytes.write_bytes(b"\x81\x7f\xff\xfe")
    toctou_link = src_dir / "later_link.txt"
    toctou_link.write_text("before link", encoding="utf-8")
    locked_read = src_dir / "locked.txt"
    locked_read.write_text("secret", encoding="utf-8")

    stream = merge_files(
        [good, missing, folder, bad_bytes, toctou_link, locked_read],
        out_dir,
        state_root=state,
        now=_fixed_now,
    )
    toctou_link.unlink()
    toctou_link.symlink_to(good)

    real_read_bytes = Path.read_bytes

    class _LockedPermissionError(PermissionError):
        winerror: int = 32

    def fake_read_bytes(self: Path) -> bytes:
        if self == locked_read.resolve():
            raise _LockedPermissionError(errno.EACCES, "localized denied")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)

    progress, result = _collect(stream)
    assert len(progress) == 6
    assert result.batch.succeeded == 1
    assert result.batch.failures == (
        ItemFailure(missing.resolve(), "path was not found"),
        ItemFailure(folder.resolve(), "the path is a directory"),
        ItemFailure(bad_bytes.resolve(), "not UTF-8 or CP932"),
        ItemFailure(src_dir.resolve() / "later_link.txt", "links and junctions are not followed"),
        ItemFailure(locked_read.resolve(), "file is in use by another process"),
    )

    merged_text = result.output.read_text(encoding="utf-8")
    assert DEFAULT_SEPARATOR.format(name="good.txt") + "\nok line\n" in merged_text
    assert "--- missing.txt ---\n[unreadable: path was not found]\n" in merged_text
    assert "--- subfolder ---\n[unreadable: the path is a directory]\n" in merged_text
    assert "--- binary.bin ---\n[unreadable: not UTF-8 or CP932]\n" in merged_text
    assert (
        "--- later_link.txt ---\n[unreadable: links and junctions are not followed]\n"
        in merged_text
    )
    assert "--- locked.txt ---\n[unreadable: file is in use by another process]\n" in merged_text


def test_merge_files_dry_run_skips_writes_locks_and_history(tmp_path: Path) -> None:
    state = tmp_path / "state"
    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    out_dir = tmp_path / "dry_out"
    stamp = _fixed_now().astimezone().strftime("%Y%m%d_%H%M%S")
    planned_out = out_dir.resolve() / f"merged_{stamp}.txt"

    with target_lock(planned_out, state_root=state):
        progress, result = _collect(
            merge_files([src], out_dir, dry_run=True, state_root=state, now=_fixed_now)
        )

    assert len(progress) == 1
    assert result.dry_run
    assert result.output == planned_out
    assert result.batch.succeeded == 1
    assert not out_dir.exists()
    assert not HistoryStore(state_root=state).path.exists()


def test_merge_files_validates_inputs_and_overwrite(tmp_path: Path) -> None:
    src = tmp_path / "input.txt"
    src.write_text("content", encoding="utf-8")
    out_dir = tmp_path / "out"

    with pytest.raises(InputError, match="Specify at least one input file"):
        merge_files([], out_dir)
    with pytest.raises(InputError, match="Output directory must not be empty"):
        merge_files([src], Path("   "))
    with pytest.raises(InputError, match="Input file path must not be empty"):
        merge_files([Path("   ")], out_dir)
    with pytest.raises(InputError, match="Invalid separator format"):
        merge_files([src], out_dir, separator="--- {unknown} ---")
    with pytest.raises(InputError, match="Invalid separator format"):
        merge_files([src], out_dir, separator="--- {name ---")

    file_as_dir = tmp_path / "not_a_dir.txt"
    file_as_dir.write_text("x", encoding="utf-8")
    with pytest.raises(InputError, match="not a directory"):
        merge_files([src], file_as_dir)

    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    link_dir = tmp_path / "link_dir"
    link_dir.symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(InputError, match="Links and junctions"):
        merge_files([src], link_dir)

    link_input = tmp_path / "link_input.txt"
    link_input.symlink_to(src)
    with pytest.raises(InputError, match="Links and junctions"):
        merge_files([link_input], out_dir)

    stamp = _fixed_now().astimezone().strftime("%Y%m%d_%H%M%S")
    same_as_output = out_dir / f"merged_{stamp}.txt"
    with pytest.raises(InputError, match="Output path matches an input file"):
        merge_files([same_as_output], out_dir, now=_fixed_now)

    state = tmp_path / "state"
    _collect(merge_files([src], out_dir, state_root=state, now=_fixed_now))
    with pytest.raises(InputError, match="already exists"):
        merge_files([src], out_dir, state_root=state, now=_fixed_now)

    src.write_text("updated", encoding="utf-8")
    _, overwritten = _collect(
        merge_files([src], out_dir, overwrite=True, state_root=state, now=_fixed_now)
    )
    assert overwritten.batch.succeeded == 1
    assert "updated" in overwritten.output.read_text(encoding="utf-8")


def test_merge_files_lock_contention_toctou_mkdir_and_write_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    src = tmp_path / "input.txt"
    src.write_text("hello", encoding="utf-8")
    out_dir = tmp_path / "locked"
    stamp = _fixed_now().astimezone().strftime("%Y%m%d_%H%M%S")
    target = out_dir.resolve() / f"merged_{stamp}.txt"

    with target_lock(target, state_root=state):
        stream = merge_files([src], out_dir, state_root=state, now=_fixed_now)
        with pytest.raises(OperationError, match="Target is locked"):
            list(stream)

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"

    toctou_dir = tmp_path / "toctou"
    stream_toctou = merge_files([src], toctou_dir, state_root=state, now=_fixed_now)
    toctou_dir.mkdir()
    (toctou_dir / f"merged_{stamp}.txt").write_text("race", encoding="utf-8")
    with pytest.raises(InputError, match="already exists"):
        list(stream_toctou)

    link_out_dir = tmp_path / "link_out"
    stream_link_out = merge_files(
        [src], link_out_dir, overwrite=True, state_root=state, now=_fixed_now
    )
    link_out_dir.mkdir()
    (link_out_dir / f"merged_{stamp}.txt").symlink_to(src)
    with pytest.raises(OperationError, match="Links and junctions"):
        list(stream_link_out)

    cant_create = tmp_path / "cant_create"
    real_mkdir = Path.mkdir

    def fail_mkdir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if self == cant_create.resolve():
            raise OSError(errno.EACCES, "localized denied")
        real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    with pytest.raises(OperationError, match="access is denied"):
        list(merge_files([src], cant_create, state_root=state, now=_fixed_now))
    monkeypatch.undo()

    write_fail_dir = tmp_path / "write_fail"
    real_write_text = Path.write_text

    def fail_write_text(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if self.parent == write_fail_dir.resolve():
            raise OSError(errno.ENOSPC, "disk full")
        return real_write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", fail_write_text)
    with pytest.raises(OperationError, match="no space left on device"):
        list(merge_files([src], write_fail_dir, state_root=state, now=_fixed_now))


def test_merge_files_cancellation_and_generator_close(tmp_path: Path) -> None:
    state = tmp_path / "state"
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("one", encoding="utf-8")
    b.write_text("two", encoding="utf-8")

    immediate_dir = tmp_path / "immediate_cancel"
    c_prog, c_res = _collect(
        merge_files([a, b], immediate_dir, state_root=state, cancel=lambda: True, now=_fixed_now)
    )
    assert c_prog == []
    assert c_res.batch.succeeded == 0
    assert c_res.batch.cancelled
    assert not c_res.output.exists()

    after_one_dir = tmp_path / "after_one_cancel"
    checks = 0

    def stop_after_one() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    p1, r1 = _collect(
        merge_files([a, b], after_one_dir, state_root=state, cancel=stop_after_one, now=_fixed_now)
    )
    assert len(p1) == 1
    assert r1.batch.succeeded == 1
    assert r1.batch.cancelled
    assert r1.output.is_file()

    aborted_dir = tmp_path / "aborted"
    gen = merge_files([a, b], aborted_dir, state_root=state)
    first = next(gen)
    assert isinstance(first, Progress)
    cast("Generator[Progress | FileMergeResult, None, None]", gen).close()
    latest = HistoryStore(state_root=state).list_runs()[0]
    assert latest.status == "cancelled"
    assert latest.succeeded == 1


def test_os_error_reason_mappings_and_fallback() -> None:
    assert file_merge._os_error_reason(OSError(errno.EACCES, "denied")) == "access is denied"
    assert file_merge._os_error_reason(OSError(errno.ENOLCK, "no locks")) == "no locks"
