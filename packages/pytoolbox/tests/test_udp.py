import errno
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, timezone

import pytest

from pytoolbox.errors import InputError, OperationError
from pytoolbox.results import LogEvent, clock
from pytoolbox.tools.system.udp import (
    BUFFER,
    POLL,
    SHOWN_BYTES,
    UdpResult,
    describe,
    listen_udp,
)

SENDER = ("192.168.0.5", 51234)
T0 = datetime(2026, 10, 28, 3, 4, 5, 678_901, tzinfo=UTC)

type Arrival = tuple[bytes, tuple[str, int]] | OSError


class FakeSocket:
    """Plays a script of arrivals, then times out forever, as an idle socket does."""

    def __init__(self, arrivals: Iterable[Arrival] = (), bind_error: OSError | None = None) -> None:
        self.arrivals = list(arrivals)
        self.bind_error = bind_error
        self.bound: tuple[str, int] | None = None
        self.timeout: float | None = None
        self.sizes: list[int] = []
        self.closed = False

    def bind(self, address: tuple[str, int], /) -> None:
        if self.bind_error is not None:
            raise self.bind_error
        self.bound = address

    def settimeout(self, value: float | None, /) -> None:
        self.timeout = value

    def recvfrom(self, bufsize: int, /) -> tuple[bytes, tuple[str, int]]:
        self.sizes.append(bufsize)
        if not self.arrivals:
            raise TimeoutError
        arrival = self.arrivals.pop(0)
        if isinstance(arrival, OSError):
            raise arrival
        return arrival

    def close(self) -> None:
        self.closed = True


def _run(sock: FakeSocket, stop_after_calls: int, port: int = 9000) -> list[LogEvent | UdpResult]:
    def cancel() -> bool:
        return len(sock.sizes) >= stop_after_calls

    return list(listen_udp(port, cancel=cancel, make_socket=lambda: sock, now=lambda: T0))


def test_describe_shows_length_hex_and_text() -> None:
    assert describe(b"Hello", SENDER) == (
        'from 192.168.0.5:51234  5 bytes  hex 48656c6c6f  text "Hello"'
    )
    assert describe("日本".encode(), SENDER).endswith('text "日本"')


def test_describe_says_not_utf8() -> None:
    assert describe(b"\xff\x00", SENDER) == "from 192.168.0.5:51234  2 bytes  hex ff00  not UTF-8"


def test_describe_shows_the_first_bytes_and_counts_the_rest() -> None:
    line = describe(b"a" * (SHOWN_BYTES + 5), SENDER)

    assert line.startswith(f"from 192.168.0.5:51234  {SHOWN_BYTES + 5} bytes  hex ")
    assert line.count("61") >= SHOWN_BYTES
    assert line.endswith(f'"{"a" * SHOWN_BYTES}"  (+5 bytes)')


def test_listen_binds_every_interface_and_logs_each_datagram() -> None:
    sock = FakeSocket([(b"one", SENDER), (b"two", ("10.0.0.1", 1))])

    values = _run(sock, stop_after_calls=3)

    assert sock.bound == ("0.0.0.0", 9000)  # noqa: S104 - the address the tool listens on
    assert sock.timeout == POLL
    assert set(sock.sizes) == {BUFFER}
    assert values == [
        LogEvent(T0, 'from 192.168.0.5:51234  3 bytes  hex 6f6e65  text "one"'),
        LogEvent(T0, 'from 10.0.0.1:1  3 bytes  hex 74776f  text "two"'),
        UdpResult(9000, 2),
    ]
    assert sock.closed


def test_timeouts_and_connection_resets_are_waited_through() -> None:
    sock = FakeSocket([TimeoutError(), ConnectionResetError(10054, "reset"), (b"x", SENDER)])

    values = _run(sock, stop_after_calls=4)

    assert values[-1] == UdpResult(9000, 1)


def test_another_receive_error_is_an_operation_error_and_closes_the_socket() -> None:
    sock = FakeSocket([OSError(10050, "Network is down")])

    with pytest.raises(
        OperationError, match=r"Receiving on 0\.0\.0\.0:9000 failed: Network is down"
    ):
        _run(sock, stop_after_calls=5)
    assert sock.closed


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_a_port_out_of_range_is_an_input_error(port: int) -> None:
    with pytest.raises(InputError, match="outside 1 to 65535"):
        listen_udp(port, make_socket=FakeSocket)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (OSError(errno.EADDRINUSE, "使用中"), "the port is in use"),
        (OSError(errno.EACCES, "拒否"), "access to the port is denied"),
        (OSError(10013, "拒否"), "access to the port is denied"),
        (OSError(errno.EINVAL, "Invalid argument"), "Invalid argument"),
    ],
)
def test_a_port_that_cannot_be_bound_fails_in_english_before_streaming(
    error: OSError, reason: str
) -> None:
    sock = FakeSocket(bind_error=error)

    with pytest.raises(OperationError, match=rf"Cannot listen on 0\.0\.0\.0:9000: {reason}\."):
        listen_udp(9000, make_socket=lambda: sock)
    assert sock.closed


def test_clock_writes_local_time_with_milliseconds() -> None:
    nine = timezone(timedelta(hours=9))

    assert clock(T0) == f"{T0.astimezone():%H:%M:%S}.678"
    assert clock(T0.astimezone(nine)) == clock(T0)
