"""Receiving UDP datagrams and logging each one."""

import errno
import json
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pytoolbox.errors import InputError, OperationError
from pytoolbox.results import Cancel, LogEvent

HOST = "0.0.0.0"  # noqa: S104 - the tool listens on every interface (docs/design.ja.md §9)
DEFAULT_PORT = 9000
BUFFER = 65535
"""Bytes read per datagram. Windows fails a datagram larger than the buffer (ADR 0015)."""
SHOWN_BYTES = 1024
"""Bytes of each datagram that the log shows as hex and text."""
POLL = 0.2
"""Seconds between checks of `cancel` while no datagram arrives."""
_MAX_PORT = 65535


class Socket(Protocol):
    """The part of a UDP socket the listener uses, so tests can pass a fake."""

    def bind(self, address: tuple[str, int], /) -> None:
        """Bind to a host and port."""
        ...

    def settimeout(self, value: float | None, /) -> None:
        """Make `recvfrom` raise `TimeoutError` after this many seconds."""
        ...

    def recvfrom(self, bufsize: int, /) -> tuple[bytes, tuple[str, int]]:
        """Receive one datagram and its sender."""
        ...

    def close(self) -> None:
        """Close the socket."""
        ...


def udp_socket() -> Socket:
    """Make an IPv4 UDP socket."""
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


@dataclass(frozen=True)
class UdpResult:
    """The end of a listening session, yielded last.

    Attributes:
        port: The port it listened on.
        received: How many datagrams arrived.
    """

    port: int
    received: int


def describe(data: bytes, sender: tuple[str, int]) -> str:
    """Write one datagram as a log line, without the time.

    Args:
        data: The datagram.
        sender: The sender's address and port.

    Returns:
        `from <ip>:<port>  <n> bytes  hex <hex>  text <JSON string>`, with `not UTF-8` in
        place of the text when the shown bytes do not decode, and `(+N bytes)` when the
        datagram is longer than `SHOWN_BYTES`.
    """
    shown = data[:SHOWN_BYTES]
    try:
        text = "text " + json.dumps(shown.decode("utf-8"), ensure_ascii=False)
    except UnicodeDecodeError:
        text = "not UTF-8"
    line = f"from {sender[0]}:{sender[1]}  {len(data)} bytes  hex {shown.hex()}  {text}"
    if hidden := len(data) - len(shown):
        line += f"  (+{hidden} bytes)"
    return line


_WSAEACCES = 10013
"""Windows: the port is reserved or another program holds it exclusively."""


def _bind_reason(error: OSError) -> str:
    """Say in English why a bind failed. Windows words `strerror` in the PC's language."""
    if error.errno == errno.EADDRINUSE:
        return "the port is in use"
    if error.errno in {errno.EACCES, _WSAEACCES}:
        return "access to the port is denied"
    return error.strerror or str(error)


def listen_udp(
    port: int = DEFAULT_PORT,
    *,
    cancel: Cancel | None = None,
    make_socket: Callable[[], Socket] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Iterator[LogEvent | UdpResult]:
    """Listen for UDP datagrams on `0.0.0.0` and log each one.

    The socket is bound when this is called, so a bad or busy port fails before anything
    streams. The returned iterator yields one `LogEvent` per datagram until `cancel` returns
    True, closes the socket, and yields one `UdpResult` last.

    Args:
        port: The port, from 1 to 65535.
        cancel: Checked at least every `POLL` seconds.
        make_socket: Makes the socket. `None` means `udp_socket`, looked up when called.
        now: The time of each event, timezone-aware UTC.

    Returns:
        The iterator.

    Raises:
        InputError: The port is outside 1 to 65535.
        OperationError: The port cannot be bound, for example because it is in use.
    """
    if not 1 <= port <= _MAX_PORT:
        msg = f"Port {port} is outside 1 to 65535."
        raise InputError(msg)
    sock = (make_socket or udp_socket)()
    try:
        sock.bind((HOST, port))
    except OSError as error:
        sock.close()
        msg = f"Cannot listen on {HOST}:{port}: {_bind_reason(error)}."
        raise OperationError(msg) from error
    sock.settimeout(POLL)
    return _receive(sock, port, cancel or (lambda: False), now)


def _receive(
    sock: Socket, port: int, cancel: Cancel, now: Callable[[], datetime]
) -> Iterator[LogEvent | UdpResult]:
    received = 0
    try:
        while not cancel():
            try:
                data, sender = sock.recvfrom(BUFFER)
            except TimeoutError:
                continue
            except ConnectionResetError:
                # Windows reports an ICMP "port unreachable" on the next receive. Nothing came.
                continue
            except OSError as error:
                msg = f"Receiving on {HOST}:{port} failed: {error.strerror or error}."
                raise OperationError(msg) from error
            received += 1
            yield LogEvent(now(), describe(data, sender))
    finally:
        sock.close()
    yield UdpResult(port, received)
