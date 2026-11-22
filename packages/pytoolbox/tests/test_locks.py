import errno
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO

import pytest

from pytoolbox import locks
from pytoolbox.errors import OperationError
from pytoolbox.locks import lock_path, target_lock
from pytoolbox.state import state_dir


def test_state_dir_windows_uses_localappdata_or_fallback(tmp_path: Path) -> None:
    local = tmp_path / "local"
    home = tmp_path / "home"

    assert state_dir(env={"LOCALAPPDATA": str(local)}, platform="win32", home=home) == (
        local / "pytoolbox"
    )
    assert state_dir(env={}, platform="win32", home=home) == (
        home / "AppData" / "Local" / "pytoolbox"
    )
    assert state_dir(env={"LOCALAPPDATA": ""}, platform="win32", home=home) == (
        home / "AppData" / "Local" / "pytoolbox"
    )


def test_state_dir_posix_uses_xdg_state_home_or_fallback(tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"

    assert state_dir(env={"XDG_STATE_HOME": str(xdg)}, platform="linux", home=home) == (
        xdg / "pytoolbox"
    )
    assert state_dir(env={}, platform="linux", home=home) == (
        home / ".local" / "state" / "pytoolbox"
    )
    assert state_dir(env={"XDG_STATE_HOME": ""}, platform="linux", home=home) == (
        home / ".local" / "state" / "pytoolbox"
    )


def test_lock_path_hashes_resolved_target_and_respects_platform_casing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    target = tmp_path / "sub" / ".." / "Out.txt"
    resolved = target.resolve(strict=False)
    expected_key = str(resolved).lower() if sys.platform == "win32" else str(resolved)
    expected_digest = hashlib.sha256(expected_key.encode("utf-8")).hexdigest()

    assert lock_path(target, state_root=state) == state / "locks" / f"{expected_digest}.lock"
    assert lock_path(resolved, state_root=state) == lock_path(target, state_root=state)

    upper = tmp_path / "CaseA.txt"
    lower = tmp_path / "casea.txt"
    assert lock_path(upper, state_root=state, platform="win32") == lock_path(
        lower, state_root=state, platform="win32"
    )
    assert lock_path(upper, state_root=state, platform="linux") != lock_path(
        lower, state_root=state, platform="linux"
    )

    if sys.platform == "win32":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "env_state"))
    else:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "env_state"))
    assert lock_path(target).parent == tmp_path / "env_state" / "pytoolbox" / "locks"


def test_target_lock_acquires_releases_and_keeps_lock_file(tmp_path: Path) -> None:
    state = tmp_path / "state"
    target = tmp_path / "out.csv"

    with target_lock(target, state_root=state) as held:
        assert held == (lock_path(target, state_root=state),)
        assert held[0].is_file()

        with (
            pytest.raises(OperationError, match="Target is locked by another run:"),
            target_lock(target, state_root=state),
        ):
            pass

    assert held[0].exists()
    with target_lock(target, state_root=state) as reacquired:
        assert reacquired == held


def test_target_lock_deduplicates_sorts_and_rolls_back_on_partial_failure(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    expected_sorted = tuple(
        sorted(
            (lock_path(first, state_root=state), lock_path(second, state_root=state)),
            key=lambda item: item.name,
        )
    )

    with target_lock([second, first, second], state_root=state) as held:
        assert held == expected_sorted

    with target_lock(second, state_root=state):
        with (
            pytest.raises(OperationError, match="Target is locked by another run:"),
            target_lock([first, second], state_root=state),
        ):
            pass

        # The first lock acquired before hitting `second` must already be released.
        with target_lock(first, state_root=state) as acquired:
            assert acquired == (lock_path(first, state_root=state),)


def test_target_lock_empty_iterable_yields_empty_and_creates_nothing(tmp_path: Path) -> None:
    state = tmp_path / "state"

    with target_lock([], state_root=state) as held:
        assert held == ()

    assert not state.exists()


def test_target_lock_blocks_across_processes_and_releases_when_child_dies(tmp_path: Path) -> None:
    state = tmp_path / "state"
    target = tmp_path / "shared.bin"
    script = "\n".join(
        [
            "import pathlib, sys, time",
            "from pytoolbox.locks import target_lock",
            "target, state = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])",
            "with target_lock(target, state_root=state):",
            "    print('locked', flush=True)",
            "    time.sleep(30)",
        ]
    )
    proc = subprocess.Popen(  # noqa: S603 - test launches current Python interpreter with inline script
        [sys.executable, "-c", script, str(target), str(state)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        ready = proc.stdout.readline().strip()
        assert ready == "locked", proc.stderr.read() if proc.stderr is not None else ""

        with (
            pytest.raises(OperationError, match="Target is locked by another run:"),
            target_lock(target, state_root=state),
        ):
            pass
    finally:
        proc.kill()
        proc.wait(timeout=5)

    with target_lock(target, state_root=state) as held:
        assert held == (lock_path(target, state_root=state),)


def test_target_lock_reports_os_errors_in_english(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_as_state = tmp_path / "not_a_dir"
    file_as_state.write_text("x", encoding="utf-8")

    with (
        pytest.raises(OperationError, match="Cannot prepare lock directory"),
        target_lock(tmp_path / "a.txt", state_root=file_as_state),
    ):
        pass

    state = tmp_path / "state"
    target = tmp_path / "b.txt"
    lock_file = lock_path(target, state_root=state)
    lock_file.mkdir(parents=True)

    with (
        pytest.raises(OperationError, match="Cannot open lock file"),
        target_lock(target, state_root=state),
    ):
        pass

    def fail_lock(_handle: BinaryIO) -> None:
        raise OSError(errno.ENOLCK, "no locks left")

    monkeypatch.setattr(locks, "_lock_fd", fail_lock)
    other = tmp_path / "c.txt"
    with (
        pytest.raises(OperationError, match=r"Cannot lock .*no locks left"),
        target_lock(other, state_root=state),
    ):
        pass

    assert locks._os_error_reason(OSError(errno.EACCES, "localized")) == "access is denied"
