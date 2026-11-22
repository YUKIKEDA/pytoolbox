import errno
import socket
from types import TracebackType

import pytest

from pytoolbox.errors import InputError, OperationError
from pytoolbox.runner import RunResult
from pytoolbox.tools.system.network import (
    DEFAULT_TIMEOUT,
    PingResult,
    PortResult,
    check_port,
    ping_host,
)


def test_ping_host_builds_windows_command_and_parses_latency() -> None:
    calls: list[tuple[list[str], float]] = []

    def fake_run(cmd: list[str], *, timeout: float) -> RunResult:
        calls.append((cmd, timeout))
        return RunResult(
            tuple(cmd),
            0,
            "Reply from 127.0.0.1: bytes=32 time<1ms TTL=128\n",
            "",
        )

    result = ping_host("127.0.0.1", run=fake_run, platform="win32")

    assert calls == [(["ping", "-n", "1", "-w", "3000", "127.0.0.1"], 5.0)]
    assert result == PingResult(
        host="127.0.0.1",
        reachable=True,
        latency_ms=1.0,
        output="Reply from 127.0.0.1: bytes=32 time<1ms TTL=128",
    )


def test_ping_host_builds_posix_command_with_ceiling_timeout() -> None:
    calls: list[tuple[list[str], float]] = []

    def fake_run(cmd: list[str], *, timeout: float) -> RunResult:
        calls.append((cmd, timeout))
        return RunResult(
            tuple(cmd),
            0,
            "64 bytes from 1.1.1.1: icmp_seq=1 ttl=56 TIME = 14.25 MS\n",
            "",
        )

    result = ping_host("1.1.1.1", timeout=5.2, run=fake_run, platform="linux")

    assert calls == [(["ping", "-c", "1", "-W", "6", "1.1.1.1"], 7.2)]
    assert result.reachable is True
    assert result.latency_ms == 14.25


def test_ping_host_enforces_minimum_ping_timeout_argument() -> None:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], *, timeout: float) -> RunResult:
        del timeout
        seen.append(cmd)
        return RunResult(tuple(cmd), 0, "", "")

    win_result = ping_host("localhost", timeout=0.0001, run=fake_run, platform="win32")
    posix_result = ping_host("localhost", timeout=0.2, run=fake_run, platform="darwin")

    assert seen == [
        ["ping", "-n", "1", "-w", "1", "localhost"],
        ["ping", "-c", "1", "-W", "1", "localhost"],
    ]
    assert win_result == PingResult("localhost", reachable=True, latency_ms=None, output="")
    assert posix_result == PingResult("localhost", reachable=True, latency_ms=None, output="")


def test_ping_host_handles_malformed_latency_match() -> None:
    def fake_run(cmd: list[str], *, timeout: float) -> RunResult:
        del timeout
        return RunResult(tuple(cmd), 0, "time=1.2.3 ms", "")

    result = ping_host("localhost", run=fake_run, platform="linux")

    assert result == PingResult(
        "localhost", reachable=True, latency_ms=None, output="time=1.2.3 ms"
    )


def test_ping_host_returns_unreachable_when_exit_code_is_nonzero() -> None:
    def fake_run(cmd: list[str], *, timeout: float) -> RunResult:
        del timeout
        return RunResult(tuple(cmd), 1, "Request timed out.\r\n", "warning\n")

    result = ping_host("192.0.2.1", run=fake_run, platform="win32")

    assert result == PingResult(
        host="192.0.2.1",
        reachable=False,
        latency_ms=None,
        output="Request timed out.\nwarning",
    )


def test_ping_host_treats_runner_timeout_as_unreachable() -> None:
    def fake_run(cmd: list[str], *, timeout: float) -> RunResult:
        del cmd, timeout
        msg = "Command timed out after 5 s."
        raise OperationError(msg)

    result = ping_host("192.0.2.1", run=fake_run, platform="linux")

    assert result == PingResult(
        host="192.0.2.1",
        reachable=False,
        latency_ms=None,
        output="Command timed out after 5 s.",
    )


def test_ping_host_propagates_non_timeout_operation_error() -> None:
    def fake_run(cmd: list[str], *, timeout: float) -> RunResult:
        del cmd, timeout
        msg = "Failed to run command 'ping': command or path was not found."
        raise OperationError(msg)

    with pytest.raises(OperationError, match="command or path was not found"):
        ping_host("localhost", run=fake_run, platform="linux")


@pytest.mark.parametrize("host", ["", "   ", "bad host", "host\t1", "host\x00", "-n"])
def test_ping_host_rejects_invalid_hosts(host: str) -> None:
    with pytest.raises(InputError):
        ping_host(host, run=lambda *_args, **_kwargs: RunResult((), 0, "", ""))


@pytest.mark.parametrize(
    "timeout",
    [0.0, -1.0, float("nan"), float("inf"), float("-inf"), True, False],
)
def test_ping_host_rejects_invalid_timeouts(timeout: float) -> None:
    with pytest.raises(InputError):
        ping_host("localhost", timeout=timeout)


def test_check_port_reports_open_port_and_elapsed_ms() -> None:
    calls: list[tuple[str, int, float]] = []

    def fake_connect(host: str, port: int, timeout: float) -> None:
        calls.append((host, port, timeout))

    result = check_port("example.com", 443, timeout=2.5, connect=fake_connect)

    assert calls == [("example.com", 443, 2.5)]
    assert result.host == "example.com"
    assert result.port == 443
    assert result.is_open is True
    assert result.latency_ms is not None
    assert result.latency_ms >= 0.0
    assert result.detail == "open"


def test_check_port_uses_socket_create_connection_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[tuple[str, int], float]] = []
    closed = False

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            nonlocal closed
            del exc_type, exc, tb
            closed = True

    def fake_create_connection(
        address: tuple[str, int], timeout: float | None = None
    ) -> FakeConnection:
        assert timeout is not None
        seen.append((address, timeout))
        return FakeConnection()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    result = check_port("127.0.0.1", 8000)

    assert seen == [(("127.0.0.1", 8000), DEFAULT_TIMEOUT)]
    assert closed is True
    assert result.is_open is True
    assert result.detail == "open"


@pytest.mark.parametrize(
    ("error", "expected_detail"),
    [
        (TimeoutError(), "connection timed out"),
        (socket.gaierror(socket.EAI_NONAME, "不明なホスト"), "host name could not be resolved"),
        (ConnectionRefusedError(), "connection refused"),
        (PermissionError(), "access is denied"),
        (OSError(errno.ECONNREFUSED, "接続が拒否されました"), "connection refused"),
        (OSError(errno.ETIMEDOUT, "タイムアウトしました"), "connection timed out"),
        (OSError(errno.EHOSTUNREACH, "ホストへ到達できません"), "host is unreachable"),
        (OSError(errno.ENETUNREACH, "ネットワークへ到達できません"), "network is unreachable"),
        (OSError(errno.EACCES, "アクセス拒否"), "access is denied"),
        (OSError(10013, "アクセス拒否"), "access is denied"),
        (OSError(99999, "ローカライズされたエラー"), "connection failed"),
    ],
)
def test_check_port_maps_failures_to_fixed_english_phrases(
    error: OSError, expected_detail: str
) -> None:
    def failing_connect(host: str, port: int, timeout: float) -> None:
        del host, port, timeout
        raise error

    result = check_port("example.com", 443, connect=failing_connect)

    assert result == PortResult(
        host="example.com",
        port=443,
        is_open=False,
        latency_ms=None,
        detail=expected_detail,
    )


def _noop_connect(host: str, port: int, timeout: float) -> None:
    del host, port, timeout


@pytest.mark.parametrize("host", ["", " ", "a b", "a\x00b", "-h"])
def test_check_port_rejects_invalid_hosts(host: str) -> None:
    with pytest.raises(InputError):
        check_port(host, 80, connect=_noop_connect)


@pytest.mark.parametrize("port", [0, -1, 65536, True, False])
def test_check_port_rejects_invalid_ports(port: int) -> None:
    with pytest.raises(InputError, match="outside 1 to 65535"):
        check_port("localhost", port, connect=_noop_connect)


@pytest.mark.parametrize(
    "timeout",
    [0.0, -0.5, float("nan"), float("inf"), True],
)
def test_check_port_rejects_invalid_timeouts(timeout: float) -> None:
    with pytest.raises(InputError):
        check_port("localhost", 80, timeout=timeout, connect=_noop_connect)
