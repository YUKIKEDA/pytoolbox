"""Network connectivity checks: ICMP ping and TCP port probing."""

import errno
import math
import re
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from pytoolbox.errors import InputError, OperationError
from pytoolbox.runner import RunResult, run_command

DEFAULT_TIMEOUT = 3.0
"""Default timeout in seconds for ping and TCP port checks."""

_MAX_PORT = 65535
_WSAEACCES = 10013
_LATENCY_RE = re.compile(r"time\s*[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)

_PORT_ERROR_PHRASES: dict[int, str] = {
    errno.ECONNREFUSED: "connection refused",
    errno.ETIMEDOUT: "connection timed out",
    errno.EHOSTUNREACH: "host is unreachable",
    errno.ENETUNREACH: "network is unreachable",
    errno.EACCES: "access is denied",
    _WSAEACCES: "access is denied",
}


@dataclass(frozen=True)
class PingResult:
    """Outcome of a single ICMP ping check.

    Attributes:
        host: Target host name or IP address.
        reachable: Whether the target replied.
        latency_ms: Round-trip time in milliseconds when parsed from output, else ``None``.
        output: Combined command output or timeout message.
    """

    host: str
    reachable: bool
    latency_ms: float | None
    output: str


@dataclass(frozen=True)
class PortResult:
    """Outcome of a single TCP port probe.

    Attributes:
        host: Target host name or IP address.
        port: TCP port number checked.
        is_open: Whether a TCP connection succeeded.
        latency_ms: Elapsed connection time in milliseconds when open, else ``None``.
        detail: Fixed English status or failure phrase.
    """

    host: str
    port: int
    is_open: bool
    latency_ms: float | None
    detail: str


def _validate_host(host: str) -> str:
    if not host or any(char.isspace() for char in host) or "\x00" in host:
        msg = "Host must not be empty or contain whitespace."
        raise InputError(msg)
    if host.startswith("-"):
        msg = "Host must not start with '-'."
        raise InputError(msg)
    return host


def _validate_timeout(timeout: float) -> float:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        msg = f"Timeout must be a finite positive number of seconds, got {timeout!r}."
        raise InputError(msg)
    return float(timeout)


def _validate_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= _MAX_PORT:
        msg = f"Port {port} is outside 1 to 65535."
        raise InputError(msg)
    return port


def _combine_output(stdout: str, stderr: str) -> str:
    parts = [part.replace("\r\n", "\n").strip() for part in (stdout, stderr) if part.strip()]
    return "\n".join(parts)


def _parse_latency_ms(output: str) -> float | None:
    match = _LATENCY_RE.search(output)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _tcp_connect(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        pass


def _port_failure_detail(error: OSError) -> str:
    """Return a fixed English phrase for socket and OS connection failures."""
    if isinstance(error, TimeoutError):
        return "connection timed out"
    if isinstance(error, socket.gaierror):
        return "host name could not be resolved"
    if isinstance(error, ConnectionRefusedError):
        return "connection refused"
    if isinstance(error, PermissionError):
        return "access is denied"
    if error.errno is not None and error.errno in _PORT_ERROR_PHRASES:
        return _PORT_ERROR_PHRASES[error.errno]
    return "connection failed"


def ping_host(
    host: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    run: Callable[..., RunResult] = run_command,
    platform: str | None = None,
) -> PingResult:
    """Send one ICMP echo request to ``host`` through the subprocess runner.

    Args:
        host: Host name or IP address. Must not be empty or contain whitespace.
        timeout: Maximum wait time in seconds. Must be finite and positive.
        run: Subprocess runner callable, defaulting to :func:`pytoolbox.runner.run_command`.
        platform: Target platform identifier such as ``"win32"``. Defaults to ``sys.platform``.

    Returns:
        A :class:`PingResult` describing reachability, parsed latency in milliseconds, and output.

    Raises:
        InputError: ``host`` or ``timeout`` is invalid.
        OperationError: Starting or running the ``ping`` command failed for a reason other than
            timing out.
    """
    _validate_host(host)
    validated_timeout = _validate_timeout(timeout)
    active_platform = sys.platform if platform is None else platform
    if active_platform == "win32":
        command = ["ping", "-n", "1", "-w", str(max(1, round(validated_timeout * 1000))), host]
    else:
        command = ["ping", "-c", "1", "-W", str(max(1, math.ceil(validated_timeout))), host]

    try:
        result = run(command, timeout=max(validated_timeout + 2.0, 5.0))
    except OperationError as error:
        if "timed out" in str(error).casefold():
            return PingResult(host=host, reachable=False, latency_ms=None, output=str(error))
        raise

    output = _combine_output(result.stdout, result.stderr)
    if result.returncode != 0:
        return PingResult(host=host, reachable=False, latency_ms=None, output=output)
    return PingResult(
        host=host,
        reachable=True,
        latency_ms=_parse_latency_ms(output),
        output=output,
    )


def check_port(
    host: str,
    port: int,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    connect: Callable[[str, int, float], None] | None = None,
) -> PortResult:
    """Check whether ``host:port`` accepts a TCP connection and measure elapsed time.

    Args:
        host: Host name or IP address. Must not be empty or contain whitespace.
        port: TCP port number in ``1..65535``.
        timeout: Connection timeout in seconds. Must be finite and positive.
        connect: Connector callable taking ``(host, port, timeout)``. Defaults to
            ``socket.create_connection``.

    Returns:
        A :class:`PortResult` reporting whether the port is open, latency in ms, and detail.

    Raises:
        InputError: ``host``, ``port``, or ``timeout`` is invalid.
    """
    _validate_host(host)
    _validate_port(port)
    validated_timeout = _validate_timeout(timeout)
    connector = _tcp_connect if connect is None else connect

    started = time.perf_counter()
    try:
        connector(host, port, validated_timeout)
    except (TimeoutError, OSError) as error:
        return PortResult(
            host=host,
            port=port,
            is_open=False,
            latency_ms=None,
            detail=_port_failure_detail(error),
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return PortResult(
        host=host,
        port=port,
        is_open=True,
        latency_ms=elapsed_ms,
        detail="open",
    )
