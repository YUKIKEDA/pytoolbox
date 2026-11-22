import errno
import threading
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from pytoolbox.results import LogEvent
from pytoolbox.tools.system import udp
from pytoolbox_gui.screens import stream_udp
from pytoolbox_gui.server import create_app
from pytoolbox_gui.sse import Report

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


class FakeSocket:
    """Receives the given datagrams, then times out until closed."""

    def __init__(
        self, datagrams: list[bytes] | None = None, bind_error: OSError | None = None
    ) -> None:
        self.datagrams = list(datagrams or [])
        self.bind_error = bind_error
        self.closed = threading.Event()

    def bind(self, address: tuple[str, int], /) -> None:
        if self.bind_error is not None:
            raise self.bind_error
        self.address = address

    def settimeout(self, value: float | None, /) -> None:
        self.timeout = value

    def recvfrom(self, bufsize: int, /) -> tuple[bytes, tuple[str, int]]:
        if self.datagrams:
            return self.datagrams.pop(0), ("127.0.0.1", 5000)
        raise TimeoutError

    def close(self) -> None:
        self.closed.set()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as test:
        yield test


def _start(client: TestClient, port: str) -> tuple[int, str]:
    response = client.post("/tools/udp/runs", data={"port": port}, headers={**ORIGIN, **HX})
    return response.status_code, response.text


def test_page_has_the_port_and_start(client: TestClient) -> None:
    pane = client.get("/tools/udp", headers=HX).text

    assert 'hx-post="/tools/udp/runs"' in pane
    assert 'name="port" type="number" value="9000"' in pane
    assert "min=" not in pane
    assert '<button type="submit">Start</button>' in pane


def test_a_run_logs_datagrams_and_stops_with_a_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sock = FakeSocket([b"Hi"])
    monkeypatch.setattr(udp, "udp_socket", lambda: sock)

    status, html = _start(client, "9100")

    assert status == 200
    assert ">Stop</button>" in html
    assert "this.childElementCount > 1000" in html
    start = html.index('sse-connect="/runs/') + len('sse-connect="/runs/')
    run_id = html[start : html.index("/events", start)]
    lines: list[str] = []
    with client.stream("GET", f"/runs/{run_id}/events") as response:
        for line in response.iter_lines():
            if line.startswith("data: <span>"):
                lines.append(line)
                if len(lines) == 2:
                    client.post(f"/runs/{run_id}/cancel", headers=ORIGIN)
            if line.startswith("data: <p>Stopped."):
                lines.append(line)
    assert "Listening on 0.0.0.0:9100" in lines[0]
    assert "from 127.0.0.1:5000  2 bytes  hex 4869  text &#34;Hi&#34;" in lines[1]
    assert lines[2] == "data: <p>Stopped. 1 datagram received on port 9100.</p>"
    assert sock.closed.is_set()


@pytest.mark.parametrize(
    ("port", "status", "message"),
    [
        ("", 422, "Port: enter a whole number"),
        ("abc", 422, "Port: enter a whole number"),
        ("70000", 422, "outside 1 to 65535"),
        ("busy", 500, "Cannot listen on 0.0.0.0:9000: the port is in use."),
    ],
)
def test_bad_ports_fail_before_the_run_starts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, port: str, status: int, message: str
) -> None:
    error = OSError(errno.EADDRINUSE, "in use")
    monkeypatch.setattr(udp, "udp_socket", lambda: FakeSocket(bind_error=error))

    got, html = _start(client, "9000" if port == "busy" else port)

    assert got == status
    assert message in html


def test_a_new_listener_stops_the_one_before_and_waits_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_socket = FakeSocket()
    sockets = iter([first_socket, FakeSocket()])
    monkeypatch.setattr(udp, "udp_socket", lambda: next(sockets))
    first = stream_udp({"port": ["9000"]})(lambda: False)
    assert isinstance(next(first), LogEvent)
    worker = threading.Thread(target=lambda: list(first))
    worker.start()

    stream_udp({"port": ["9000"]})
    worker.join(2)

    assert first_socket.closed.is_set()
    assert not worker.is_alive()


def test_the_report_counts_datagrams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(udp, "udp_socket", lambda: FakeSocket([b"a", b"b"]))
    calls = 0

    def cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls > 3

    values = list(stream_udp({"port": ["9000"]})(cancel))

    last = values[-1]
    assert isinstance(last, Report)
    assert last.template == "tools/udp-result.html"
