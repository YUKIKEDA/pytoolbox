import dataclasses
import errno
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import pytest

from pytoolbox import runner
from pytoolbox.errors import InputError, OperationError
from pytoolbox.results import Cancel
from pytoolbox.runner import RunResult, run_command, run_script, script_command, script_path


def _make_script(root: Path, relative: str, content: str = "") -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_run_result_is_frozen_with_default_cancelled() -> None:
    result = RunResult(args=("echo", "hi"), returncode=0, stdout="hi\n", stderr="")

    assert result.cancelled is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.returncode = 1  # type: ignore[misc] - verify frozen dataclass raises at runtime


def test_run_command_captures_stdout_stderr_and_returncode(tmp_path: Path) -> None:
    script = "import sys; sys.stdout.write('out\\n'); sys.stderr.write('err\\n'); sys.exit(7)"

    result = run_command([sys.executable, "-c", script], cwd=tmp_path, cancel=lambda: False)

    assert result == RunResult(
        args=(sys.executable, "-c", script),
        returncode=7,
        stdout="out\n",
        stderr="err\n",
        cancelled=False,
    )


def test_run_command_respects_cwd_and_env(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    code = (
        "import os, pathlib; print(pathlib.Path.cwd()); "
        "print(os.environ.get('PYTOOLBOX_ONLY', 'missing')); "
        "print(os.environ.get('PYTOOLBOX_OTHER', 'absent'))"
    )

    result = run_command(
        [sys.executable, "-c", code],
        cwd=workdir,
        env={"PYTOOLBOX_ONLY": "value"},
    )

    lines = result.stdout.splitlines()
    assert result.returncode == 0
    assert Path(lines[0]).resolve() == workdir.resolve()
    assert lines[1] == "value"
    assert lines[2] == "absent"


def test_run_command_decodes_with_replacement_and_custom_encoding() -> None:
    invalid_utf8 = (
        "import sys; sys.stdout.buffer.write(b'ok\\xff!'); sys.stderr.buffer.write(b'\\xfe')"
    )

    utf8_result = run_command([sys.executable, "-c", invalid_utf8])
    assert utf8_result.returncode == 0
    assert utf8_result.stdout == "ok\ufffd!"
    assert utf8_result.stderr == "\ufffd"

    latin_code = "import sys; sys.stdout.buffer.write(b'caf\\xe9')"
    latin_result = run_command([sys.executable, "-c", latin_code], encoding="latin-1")
    assert latin_result.stdout == "café"


def test_run_command_handles_large_stdout_and_stderr_without_deadlock() -> None:
    code = (
        "import sys\n"
        "chunk = 'a' * 32768\n"
        "err_chunk = 'b' * 32768\n"
        "for _ in range(12):\n"
        "    sys.stdout.write(chunk)\n"
        "    sys.stderr.write(err_chunk)\n"
        "sys.stdout.flush()\n"
        "sys.stderr.flush()\n"
    )

    result = run_command([sys.executable, "-c", code], timeout=10.0)

    assert result.returncode == 0
    assert len(result.stdout) == 12 * 32768
    assert len(result.stderr) == 12 * 32768
    assert set(result.stdout) == {"a"}
    assert set(result.stderr) == {"b"}


def test_run_command_cancel_before_launch_skips_execution(tmp_path: Path) -> None:
    marker = tmp_path / "created.txt"
    code = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran', encoding='utf-8')"

    result = run_command([sys.executable, "-c", code], cancel=lambda: True)

    assert result == RunResult(
        args=(sys.executable, "-c", code),
        returncode=-1,
        stdout="",
        stderr="",
        cancelled=True,
    )
    assert not marker.exists()


def test_run_command_cancel_during_run_terminates_child() -> None:
    checks = 0

    def cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    started = time.monotonic()
    result = run_command([sys.executable, "-c", "import time; time.sleep(30)"], cancel=cancel)
    elapsed = time.monotonic() - started

    assert result.cancelled is True
    assert result.returncode != 0
    assert elapsed < 5.0


def test_run_command_timeout_terminates_child_and_raises_operation_error() -> None:
    started = time.monotonic()

    with pytest.raises(OperationError, match=r"^Command timed out after 0\.15 s\.$"):
        run_command([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.15)

    assert time.monotonic() - started < 5.0


def test_run_command_missing_executable_raises_english_operation_error() -> None:
    with pytest.raises(
        OperationError,
        match=r"^Failed to run command 'pytoolbox_no_such_exe': command or path was not found\.$",
    ):
        run_command(["pytoolbox_no_such_exe"])


def test_run_command_kills_stubborn_child_and_handles_missing_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubbornProcess:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.stdout = None
            self.stderr = None
            self.returncode = 0
            self.terminated = False
            self.killed = False
            self._alive = True

        def poll(self) -> int | None:
            return None if self._alive else self.returncode

        def wait(self, timeout: float | None = None) -> int:
            if self._alive:
                if timeout is not None:
                    raise subprocess.TimeoutExpired(cmd="stubborn", timeout=timeout)
                return self.returncode
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True
            self._alive = False
            self.returncode = -9

    created: list[StubbornProcess] = []

    def fake_popen(*args: object, **kwargs: object) -> StubbornProcess:
        proc = StubbornProcess(*args, **kwargs)
        created.append(proc)
        return proc

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner, "_TERMINATE_GRACE_SECONDS", 0.01)

    checks = 0

    def cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    result = run_command(["stubborn"], cancel=cancel, timeout=5.0)

    assert len(created) == 1
    assert created[0].terminated is True
    assert created[0].killed is True
    assert result == RunResult(
        args=("stubborn",), returncode=-9, stdout="", stderr="", cancelled=True
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            OSError(errno.ENOENT, "指定されたファイルが見つかりません。"),
            "command or path was not found",
        ),
        (OSError(errno.EACCES, "アクセスが拒否されました。"), "access is denied"),
        (OSError(errno.EPERM, "許可されていません。"), "operation is not permitted"),
        (
            OSError(errno.ENOTDIR, "ディレクトリではありません。"),
            "a parent path is not a directory",
        ),
        (OSError(errno.EISDIR, "ディレクトリです。"), "the path is a directory"),
        (OSError(errno.ENOEXEC, "形式が正しくありません。"), "executable format is invalid"),
        (OSError(errno.EINVAL, "互換性がありません。"), "invalid command or argument"),
        (OSError(errno.ENOMEM, "メモリ不足"), "not enough memory"),
        (OSError(errno.EMFILE, "ファイルが多すぎます"), "too many open files"),
        (OSError(errno.E2BIG, "引数が長すぎます"), "argument list is too long"),
        (OSError(errno.ENOLCK, "no locks left"), "no locks left"),
    ],
)
def test_run_command_maps_os_errors_without_localized_strerror(
    monkeypatch: pytest.MonkeyPatch, error: OSError, expected: str
) -> None:
    def fail_popen(*_args: object, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(runner.subprocess, "Popen", fail_popen)

    with pytest.raises(OperationError, match=expected):
        run_command(["tool"])


@pytest.mark.parametrize(
    "bad_args",
    [
        "echo hi",
        b"echo hi",
        bytearray(b"echo hi"),
        {"echo"},
        [],
        (),
        [""],
        ["   "],
        ["\t\n"],
        ["python", 123],
        ["python\x00", "-V"],
        ["python", "arg\x00bad"],
    ],
)
def test_run_command_rejects_invalid_args(bad_args: object) -> None:
    with pytest.raises(InputError):
        run_command(cast(Sequence[str], bad_args))


@pytest.mark.parametrize(
    "bad_timeout", [0, 0.0, -1.5, float("nan"), float("inf"), float("-inf"), True]
)
def test_run_command_rejects_invalid_timeout(bad_timeout: object) -> None:
    with pytest.raises(InputError):
        run_command(["python"], timeout=cast(float, bad_timeout))


def test_run_command_rejects_invalid_cwd_encoding_and_env(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(InputError):
        run_command(["python"], cwd=missing)
    with pytest.raises(InputError):
        run_command(["python"], cwd=file_path)
    with pytest.raises(InputError):
        run_command(["python"], cwd=Path("bad\x00dir"))
    with pytest.raises(InputError):
        run_command(["python"], encoding="no-such-encoding")
    with pytest.raises(InputError):
        run_command(["python"], env={"BAD\x00KEY": "1"})
    with pytest.raises(InputError):
        run_command(["python"], env={"KEY": "BAD\x00VAL"})
    with pytest.raises(InputError):
        run_command(["python"], env=cast(Mapping[str, str], {"KEY": 123}))


def test_script_path_accepts_ps1_and_sh_and_internal_parent(tmp_path: Path) -> None:
    root = tmp_path / "native-scripts"
    ps1 = _make_script(root, "win/Check.PS1", "Write-Output 'ok'")
    sh = _make_script(root, "common/check.sh", "echo ok")

    assert script_path("win/Check.PS1", root=root) == ps1.resolve()
    assert script_path(Path("common/check.sh"), root=root) == sh.resolve()
    assert script_path("win/../common/check.sh", root=root) == sh.resolve()


def test_script_path_default_root_points_to_repo_native_scripts() -> None:
    expected_root = Path(runner.__file__).resolve().parents[4] / "native-scripts"

    assert expected_root == runner._DEFAULT_SCRIPTS_ROOT
    if not expected_root.exists():
        with pytest.raises(InputError, match="Script root directory"):
            script_path("common/check.sh")


@pytest.mark.parametrize(
    "bad_relative",
    [
        "",
        "   ",
        Path(),
        ".",
        "./",
        "win/a\x00.ps1",
        "win/check.ps1/",
        "win\\check.ps1\\",
        "/etc/passwd.sh",
        "\\root.ps1",
        "C:drive_relative.ps1",
        "../outside.sh",
        "win/../../outside.sh",
        "missing_dir/../common/check.sh",
        "win/missing.ps1",
        "win/dir.sh",
        "win/script.py",
        "win/script.bat",
        "win/no_extension",
    ],
)
def test_script_path_rejects_invalid_and_escaped_paths(
    tmp_path: Path, bad_relative: str | Path
) -> None:
    root = tmp_path / "native-scripts"
    _make_script(root, "win/real.ps1")
    _make_script(root, "common/check.sh")
    _make_script(root, "win/script.py")
    _make_script(root, "win/script.bat")
    _make_script(root, "win/no_extension")
    (root / "win" / "dir.sh").mkdir(parents=True)
    _make_script(tmp_path, "outside.sh")

    with pytest.raises(InputError):
        script_path(bad_relative, root=root)


def test_script_path_rejects_absolute_tmp_path_and_invalid_root(tmp_path: Path) -> None:
    root = tmp_path / "native-scripts"
    ps1 = _make_script(root, "win/real.ps1")

    with pytest.raises(InputError):
        script_path(ps1, root=root)
    with pytest.raises(InputError):
        script_path("win/real.ps1", root=tmp_path / "missing-root")
    with pytest.raises(InputError):
        script_path("win/real.ps1", root=ps1)


def test_script_path_rejects_symlinks_and_junctions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "native-scripts"
    outside = tmp_path / "outside"
    _make_script(outside, "evil.ps1")
    _make_script(root, "sub/ok.sh")

    # Exercise symlink and junction detection deterministically even without OS symlink privilege.
    real_is_symlink = Path.is_symlink
    real_is_junction = Path.is_junction

    def fake_symlink(path: Path) -> bool:
        return path.name in {"sym_dir", "sym_file.sh"} or real_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fake_symlink)
    _make_script(root, "sym_dir/evil.sh")
    _make_script(root, "sub/sym_file.sh")
    with pytest.raises(InputError, match="link or junction"):
        script_path("sym_dir/evil.sh", root=root)
    with pytest.raises(InputError, match="link or junction"):
        script_path("sub/sym_file.sh", root=root)
    monkeypatch.undo()

    def fake_junction(path: Path) -> bool:
        return path.name == "junc_root" or real_is_junction(path)

    junc_root = tmp_path / "junc_root"
    _make_script(junc_root, "ok.ps1")
    monkeypatch.setattr(Path, "is_junction", fake_junction)
    with pytest.raises(InputError, match="Script root directory"):
        script_path("ok.ps1", root=junc_root)
    monkeypatch.undo()

    if sys.platform == "win32":
        junction = root / "win_junc"
        comspec = shutil.which("cmd") or os.environ.get("COMSPEC", "cmd.exe")
        subprocess.run(  # noqa: S603 - test creates local NTFS junction for verification
            [comspec, "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
        )
        try:
            with pytest.raises(InputError, match="link or junction"):
                script_path("win_junc/evil.ps1", root=root)
        finally:
            junction.rmdir()
    else:
        link_dir = root / "posix_link"
        link_dir.symlink_to(outside, target_is_directory=True)
        with pytest.raises(InputError, match="link or junction"):
            script_path("posix_link/evil.ps1", root=root)


def test_script_command_builds_platform_commands_and_validates_inputs() -> None:
    ps1 = Path("/scripts/win/task.ps1")
    sh = Path("/scripts/common/task.sh")

    assert script_command(ps1, ["one", "two"], platform="win32") == (
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps1),
        "one",
        "two",
    )
    assert script_command(Path("/scripts/win/task.PS1"), (), platform="linux") == (
        "pwsh",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(Path("/scripts/win/task.PS1")),
    )
    assert script_command(sh, ("--flag",), platform="win32") == ("bash", str(sh), "--flag")
    assert script_command(sh, ("--flag",), platform="linux") == ("bash", str(sh), "--flag")

    with pytest.raises(InputError):
        script_command(ps1, cast(Sequence[str], "not-a-sequence"))
    with pytest.raises(InputError):
        script_command(ps1, cast(Sequence[str], [123]))
    with pytest.raises(InputError):
        script_command(ps1, ["bad\x00arg"])
    with pytest.raises(InputError):
        script_command(Path("bad\x00.ps1"), ())
    with pytest.raises(InputError):
        script_command(Path("task.py"), ())


def test_run_script_delegates_to_run_command_and_validates_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "native-scripts"
    ps1 = _make_script(root, "win/run.ps1")
    sh = _make_script(root, "common/run.sh")
    captured: list[dict[str, object]] = []

    def fake_run_command(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        cancel: Cancel | None = None,
        encoding: str = "utf-8",
    ) -> RunResult:
        captured.append(
            {
                "args": tuple(args),
                "cwd": cwd,
                "env": env,
                "timeout": timeout,
                "cancel": cancel,
                "encoding": encoding,
            }
        )
        return RunResult(args=tuple(args), returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(runner, "run_command", fake_run_command)

    def cancel_fn() -> bool:
        return False

    res_ps1 = run_script(
        "win/run.ps1",
        ["arg1"],
        root=root,
        cwd=tmp_path,
        env={"A": "1"},
        timeout=2.5,
        cancel=cancel_fn,
        encoding="latin-1",
    )
    assert res_ps1.stdout == "ok\n"
    assert captured[0] == {
        "args": script_command(ps1.resolve(), ["arg1"]),
        "cwd": tmp_path,
        "env": {"A": "1"},
        "timeout": 2.5,
        "cancel": cancel_fn,
        "encoding": "latin-1",
    }

    run_script("common/run.sh", (), root=root)
    assert captured[1]["args"] == ("bash", str(sh.resolve()))

    with pytest.raises(InputError):
        run_script("win/run.ps1", cast(Sequence[str], "--flag"), root=root)
    with pytest.raises(InputError):
        run_script("win/run.ps1", cast(Sequence[str], [1]), root=root)


def test_run_script_executes_native_script_when_interpreter_available(tmp_path: Path) -> None:
    root = tmp_path / "native-scripts"
    if sys.platform == "win32":
        if shutil.which("powershell.exe") is None:
            pytest.skip("powershell.exe is not available")
        _make_script(root, "win/hello.ps1", 'Write-Output "hello $($args[0])"\n')
        result = run_script("win/hello.ps1", ["world"], root=root, timeout=10.0)
    else:
        if shutil.which("bash") is None:
            pytest.skip("bash is not available")
        _make_script(root, "common/hello.sh", 'printf "hello %s\\n" "$1"\n')
        result = run_script("common/hello.sh", ["world"], root=root, timeout=10.0)

    assert result.returncode == 0
    assert result.stdout == "hello world\n"
    assert result.cancelled is False
