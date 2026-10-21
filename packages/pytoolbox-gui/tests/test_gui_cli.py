import socket
import time
from types import TracebackType

import pytest

from pytoolbox.errors import OperationError
from pytoolbox_gui import cli


class _Probe:
    def __init__(self, error: OSError | None) -> None:
        self.error = error
        self.bound: tuple[str, int] | None = None

    def __enter__(self) -> "_Probe":
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def as_socket(self, *_args: object) -> "_Probe":
        return self

    def bind(self, address: tuple[str, int]) -> None:
        if self.error is not None:
            raise self.error
        self.bound = address


def test_a_free_port_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _Probe(None)
    monkeypatch.setattr(socket, "socket", probe.as_socket)

    cli.ensure_port_free(8000)

    assert probe.bound == ("127.0.0.1", 8000)


def test_a_port_in_use_is_an_operation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _Probe(OSError("in use"))
    monkeypatch.setattr(socket, "socket", probe.as_socket)

    with pytest.raises(OperationError, match=r"Port 8000 on 127\.0\.0\.1 is in use\."):
        cli.ensure_port_free(8000)


class _Server:
    def __init__(self, *, started: bool = False, should_exit: bool = False) -> None:
        self.started = started
        self.should_exit = should_exit
        self.ran = False

    def run(self) -> None:
        self.ran = True
        self.started = True


def test_browser_opens_once_the_server_has_started() -> None:
    opened: list[str] = []

    cli._open_when_started(_Server(started=True), "http://127.0.0.1:8000/", opened.append)

    assert opened == ["http://127.0.0.1:8000/"]


def test_browser_does_not_open_when_the_server_exits_first() -> None:
    opened: list[str] = []

    cli._open_when_started(_Server(should_exit=True), "http://x/", opened.append)

    assert opened == []


def test_serve_checks_the_port_runs_the_server_and_opens_the_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[int] = []
    server = _Server()
    opened: list[str] = []
    monkeypatch.setattr(cli, "ensure_port_free", checked.append)

    def make_server(_config: object) -> _Server:
        return server

    monkeypatch.setattr(cli.uvicorn, "Server", make_server)

    cli.serve(open_browser=opened.append)

    assert checked == [8000]
    assert server.ran
    for _ in range(100):
        if opened:
            break
        time.sleep(0.01)
    assert opened == ["http://127.0.0.1:8000/"]


def test_serve_opens_no_browser_when_the_port_is_in_use(monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(_port: int) -> None:
        msg = "in use"
        raise OperationError(msg)

    opened: list[str] = []
    monkeypatch.setattr(cli, "ensure_port_free", busy)

    with pytest.raises(OperationError):
        cli.serve(open_browser=opened.append)

    assert opened == []
