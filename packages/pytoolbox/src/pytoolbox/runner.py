"""Subprocess runner for external commands and scripts under ``native-scripts/``."""

import codecs
import contextlib
import errno
import math
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from pytoolbox.errors import InputError, OperationError
from pytoolbox.results import Cancel

_POLL_INTERVAL_SECONDS = 0.02
_TERMINATE_GRACE_SECONDS = 0.5
_THREAD_JOIN_SECONDS = 1.0
_ALLOWED_SCRIPT_SUFFIXES = frozenset({".ps1", ".sh"})
_DEFAULT_SCRIPTS_ROOT = Path(__file__).resolve().parents[4] / "native-scripts"

_OS_ERROR_REASONS: dict[int, str] = {
    errno.ENOENT: "command or path was not found",
    errno.EACCES: "access is denied",
    errno.EPERM: "operation is not permitted",
    errno.ENOTDIR: "a parent path is not a directory",
    errno.EISDIR: "the path is a directory",
    errno.ENOEXEC: "executable format is invalid",
    errno.EINVAL: "invalid command or argument",
    errno.ENOMEM: "not enough memory",
    errno.EMFILE: "too many open files",
    errno.ENFILE: "too many open files",
    errno.E2BIG: "argument list is too long",
}


@dataclass(frozen=True)
class RunResult:
    """Outcome of a finished or cancelled subprocess run.

    Attributes:
        args: Command argument tuple passed to the child process.
        returncode: Exit code of the child process, or ``-1`` if cancelled before launch.
        stdout: Captured standard output decoded with replacement on errors.
        stderr: Captured standard error decoded with replacement on errors.
        cancelled: Whether the run stopped because ``cancel`` returned ``True``.
    """

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False


def _os_error_reason(error: OSError) -> str:
    """Return an English reason phrase for an OS failure when launching a process."""
    if error.errno is not None and error.errno in _OS_ERROR_REASONS:
        return _OS_ERROR_REASONS[error.errno]
    return error.strerror or str(error)


def _validate_str_sequence(args: Sequence[str], *, require_command: bool) -> tuple[str, ...]:
    if isinstance(args, str | bytes | bytearray) or not isinstance(args, Sequence):
        msg = "Arguments must be a sequence of strings, not a single string or bytes."
        raise InputError(msg)
    items: list[str] = []
    for item in args:
        if not isinstance(item, str):
            msg = "Every command argument must be a string."
            raise InputError(msg)
        if "\x00" in item:
            msg = "Command arguments must not contain NUL bytes."
            raise InputError(msg)
        items.append(item)
    if require_command:
        if not items:
            msg = "Command arguments must not be empty."
            raise InputError(msg)
        if not items[0].strip():
            msg = "Executable name must not be empty or whitespace."
            raise InputError(msg)
    return tuple(items)


def _validate_run_options(
    cwd: Path | None,
    env: Mapping[str, str] | None,
    timeout: float | None,
    encoding: str,
) -> dict[str, str] | None:
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        msg = f"Timeout must be a finite positive number of seconds, got {timeout!r}."
        raise InputError(msg)
    if cwd is not None and ("\x00" in str(cwd) or not cwd.is_dir()):
        msg = f"Working directory does not exist or is not a directory: {cwd}."
        raise InputError(msg)
    try:
        codecs.lookup(encoding)
    except LookupError as error:
        msg = f"Unknown text encoding: {encoding!r}."
        raise InputError(msg) from error
    if env is None:
        return None
    validated_env: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            msg = "Environment variables must map strings to strings."
            raise InputError(msg)
        if "\x00" in key or "\x00" in value:
            msg = "Environment variables must not contain NUL bytes."
            raise InputError(msg)
        validated_env[key] = value
    return validated_env


def _read_stream(stream: IO[str] | None, chunks: list[str]) -> None:
    if stream is None:
        return
    try:
        chunks.append(stream.read())
    finally:
        stream.close()


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _wait_for_process(
    process: subprocess.Popen[str],
    command: tuple[str, ...],
    stdout_chunks: list[str],
    stderr_chunks: list[str],
    stdout_thread: threading.Thread,
    stderr_thread: threading.Thread,
    *,
    timeout: float | None,
    cancel: Cancel | None,
) -> RunResult:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            process.wait()
            stdout_thread.join()
            stderr_thread.join()
            return RunResult(
                args=command,
                returncode=process.returncode,
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
            )
        if cancel is not None and cancel():
            _stop_process(process)
            stdout_thread.join(timeout=_THREAD_JOIN_SECONDS)
            stderr_thread.join(timeout=_THREAD_JOIN_SECONDS)
            return RunResult(
                args=command,
                returncode=process.returncode,
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
                cancelled=True,
            )
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process(process)
                stdout_thread.join(timeout=_THREAD_JOIN_SECONDS)
                stderr_thread.join(timeout=_THREAD_JOIN_SECONDS)
                msg = f"Command timed out after {timeout:g} s."
                raise OperationError(msg)
            wait_seconds = min(_POLL_INTERVAL_SECONDS, remaining)
        else:
            wait_seconds = _POLL_INTERVAL_SECONDS
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=wait_seconds)


def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    cancel: Cancel | None = None,
    encoding: str = "utf-8",
) -> RunResult:
    """Run an external command directly without a shell and capture its output.

    Args:
        args: Non-empty sequence of command arguments starting with the executable.
        cwd: Working directory for the child process. Defaults to the current directory.
        env: Environment mapping for the child process. Defaults to inheriting the parent.
        timeout: Maximum run duration in seconds, or ``None`` for no limit.
        cancel: Callback polled while running; when ``True``, terminates the child.
        encoding: Text encoding used to decode stdout and stderr with ``errors="replace"``.

    Returns:
        A :class:`RunResult` containing the command tuple, exit code, output, and cancel flag.

    Raises:
        InputError: ``args``, ``cwd``, ``env``, ``timeout``, or ``encoding`` is invalid.
        OperationError: The command could not be started or timed out.
    """
    command = _validate_str_sequence(args, require_command=True)
    child_env = _validate_run_options(cwd, env, timeout, encoding)
    if cancel is not None and cancel():
        return RunResult(args=command, returncode=-1, stdout="", stderr="", cancelled=True)

    try:
        process = subprocess.Popen(  # noqa: S603 - command is a validated sequence with shell=False
            command,
            cwd=cwd,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=encoding,
            errors="replace",
            shell=False,
        )
    except OSError as error:
        msg = f"Failed to run command {command[0]!r}: {_os_error_reason(error)}."
        raise OperationError(msg) from error

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_thread = threading.Thread(
        target=_read_stream, args=(process.stdout, stdout_chunks), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_read_stream, args=(process.stderr, stderr_chunks), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    try:
        return _wait_for_process(
            process,
            command,
            stdout_chunks,
            stderr_chunks,
            stdout_thread,
            stderr_thread,
            timeout=timeout,
            cancel=cancel,
        )
    finally:
        _stop_process(process)
        stdout_thread.join(timeout=_THREAD_JOIN_SECONDS)
        stderr_thread.join(timeout=_THREAD_JOIN_SECONDS)


def _is_reparse_point(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _validate_relative_script_path(relative_path: str | Path) -> Path:
    raw = str(relative_path)
    if "\x00" in raw:
        msg = "Script path must not contain NUL bytes."
        raise InputError(msg)
    if isinstance(relative_path, str):
        if not relative_path.strip():
            msg = "Script path must not be empty."
            raise InputError(msg)
        if relative_path.endswith(("/", "\\")):
            msg = f"Script path must name a file, not a directory: {relative_path}."
            raise InputError(msg)
    candidate = Path(relative_path)
    if not candidate.parts:
        msg = "Script path must not be empty."
        raise InputError(msg)
    if candidate.is_absolute() or bool(candidate.drive) or bool(candidate.root):
        msg = f"Script path must be relative: {relative_path}."
        raise InputError(msg)
    return candidate


def script_path(relative_path: str | Path, *, root: Path | None = None) -> Path:
    """Resolve and validate a script path inside ``native-scripts/``.

    Args:
        relative_path: Relative path to a ``.ps1`` or ``.sh`` script under ``root``.
        root: Scripts root directory. Defaults to ``native-scripts`` at the repository root.

    Returns:
        Resolved path to the validated script file inside ``root``.

    Raises:
        InputError: The path is empty, absolute, escapes ``root``, traverses a symlink or
            junction, is missing or not a file, or has an unsupported extension.
    """
    candidate = _validate_relative_script_path(relative_path)
    script_root = _DEFAULT_SCRIPTS_ROOT if root is None else root
    if "\x00" in str(script_root) or _is_reparse_point(script_root) or not script_root.is_dir():
        msg = f"Script root directory does not exist or is not a directory: {script_root}."
        raise InputError(msg)

    current = script_root
    for part in candidate.parts:
        if part == "..":
            if current == script_root or not current.is_dir():
                msg = f"Script path escapes the scripts root: {relative_path}."
                raise InputError(msg)
            current = current.parent
            continue
        current = current / part
        if _is_reparse_point(current):
            msg = f"Script path must not traverse a link or junction: {current}."
            raise InputError(msg)

    if current.suffix.lower() not in _ALLOWED_SCRIPT_SUFFIXES:
        msg = f"Unsupported script extension {current.suffix!r}; allowed: .ps1, .sh."
        raise InputError(msg)
    if not current.is_file():
        msg = f"Script file was not found: {current}."
        raise InputError(msg)

    return current.resolve(strict=True)


def script_command(
    script: Path,
    args: Sequence[str] = (),
    *,
    platform: str | None = None,
) -> tuple[str, ...]:
    """Build the interpreter command tuple for a validated script file.

    Args:
        script: Path to a ``.ps1`` or ``.sh`` script.
        args: Script arguments passed after the script path.
        platform: Target platform identifier such as ``"win32"``. Defaults to ``sys.platform``.

    Returns:
        Argument tuple suitable for :func:`run_command`.

    Raises:
        InputError: ``args`` is invalid, ``script`` contains NUL bytes, or the suffix is
            not ``.ps1`` or ``.sh``.
    """
    validated_args = _validate_str_sequence(args, require_command=False)
    if "\x00" in str(script):
        msg = "Script path must not contain NUL bytes."
        raise InputError(msg)
    suffix = script.suffix.lower()
    if suffix == ".ps1":
        active_platform = sys.platform if platform is None else platform
        shell = "powershell.exe" if active_platform == "win32" else "pwsh"
        return (
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *validated_args,
        )
    if suffix == ".sh":
        return ("bash", str(script), *validated_args)
    msg = f"Unsupported script extension {script.suffix!r}; allowed: .ps1, .sh."
    raise InputError(msg)


def run_script(
    relative_path: str | Path,
    args: Sequence[str] = (),
    *,
    root: Path | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    cancel: Cancel | None = None,
    encoding: str = "utf-8",
) -> RunResult:
    """Run a validated script under ``native-scripts/`` using its platform interpreter.

    Args:
        relative_path: Relative path to a ``.ps1`` or ``.sh`` script under ``root``.
        args: Sequence of string arguments forwarded to the script.
        root: Scripts root directory. Defaults to ``native-scripts`` at the repository root.
        cwd: Working directory for the child process.
        env: Environment mapping for the child process.
        timeout: Maximum run duration in seconds, or ``None`` for no limit.
        cancel: Callback polled while running; when ``True``, terminates the child.
        encoding: Text encoding used to decode stdout and stderr with ``errors="replace"``.

    Returns:
        The :class:`RunResult` from :func:`run_command`.

    Raises:
        InputError: ``relative_path``, ``args``, or runner options are invalid.
        OperationError: The interpreter could not be started or the script timed out.
    """
    validated_args = _validate_str_sequence(args, require_command=False)
    script = script_path(relative_path, root=root)
    command = script_command(script, validated_args)
    return run_command(
        command,
        cwd=cwd,
        env=env,
        timeout=timeout,
        cancel=cancel,
        encoding=encoding,
    )
