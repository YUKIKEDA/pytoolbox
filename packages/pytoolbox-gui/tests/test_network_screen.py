from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from pytoolbox.errors import OperationError
from pytoolbox.tools.system import network
from pytoolbox.tools.system.network import DEFAULT_TIMEOUT, PingResult, PortResult
from pytoolbox_gui.server import create_app

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as test:
        yield test


def test_ping_page_has_host_timeout_and_start_button(client: TestClient) -> None:
    pane = client.get("/tools/ping", headers=HX).text

    assert 'hx-post="/tools/ping"' in pane
    assert 'hx-target="#ping-result"' in pane
    assert 'hx-sync="this:replace"' in pane
    assert 'name="host"' in pane
    assert 'name="timeout" type="number" step="any"' in pane
    assert 'value="3"' in pane
    assert "min=" not in pane
    assert "max=" not in pane
    assert "required" not in pane
    assert '<button type="submit">Start</button>' in pane
    assert "Enter a host and press Start." in pane
    assert "hx-swap-oob" not in pane


def test_ping_post_renders_reachable_result_and_clears_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, float]] = []

    def fake_ping(host: str, *, timeout: float = DEFAULT_TIMEOUT) -> PingResult:
        calls.append((host, timeout))
        return PingResult(host, reachable=True, latency_ms=12.34, output="<b>ok</b>")

    monkeypatch.setattr(network, "ping_host", fake_ping)

    response = client.post(
        "/tools/ping", data={"host": " 127.0.0.1 ", "timeout": "1.5"}, headers={**ORIGIN, **HX}
    )

    assert response.status_code == 200
    assert calls == [("127.0.0.1", 1.5)]
    assert "Reachable" in response.text
    assert "12.3 ms" in response.text
    assert "&lt;b&gt;ok&lt;/b&gt;" in response.text
    assert 'id="error"' in response.text
    assert 'hx-swap-oob="true"' in response.text


def test_ping_post_defaults_omitted_timeout_and_shows_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, float]] = []

    def fake_ping(host: str, *, timeout: float = DEFAULT_TIMEOUT) -> PingResult:
        calls.append((host, timeout))
        return PingResult(host, reachable=False, latency_ms=None, output="")

    monkeypatch.setattr(network, "ping_host", fake_ping)

    response = client.post("/tools/ping", data={"host": "192.0.2.1"}, headers={**ORIGIN, **HX})

    assert response.status_code == 200
    assert calls == [("192.0.2.1", DEFAULT_TIMEOUT)]
    assert "Unreachable" in response.text
    assert "—" in response.text
    assert "No output." in response.text


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"host": "", "timeout": "3"}, "Host must not be empty"),
        ({"host": "   ", "timeout": "3"}, "Host must not be empty"),
        ({"host": "a b", "timeout": "3"}, "Host must not be empty"),
        ({"host": "127.0.0.1", "timeout": "0"}, "Timeout must be a finite positive number"),
        ({"host": "127.0.0.1", "timeout": "abc"}, "Timeout: enter a number of seconds"),
        ({"host": "127.0.0.1", "timeout": ""}, "Timeout: enter a number of seconds"),
    ],
)
def test_ping_post_invalid_input_returns_422(
    client: TestClient, data: dict[str, str], message: str
) -> None:
    response = client.post("/tools/ping", data=data, headers={**ORIGIN, **HX})

    assert response.status_code == 422
    assert message in response.text


def test_ping_post_operation_error_returns_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(host: str, *, timeout: float = DEFAULT_TIMEOUT) -> PingResult:
        del host, timeout
        msg = "Failed to run command 'ping': command or path was not found."
        raise OperationError(msg)

    monkeypatch.setattr(network, "ping_host", boom)

    response = client.post(
        "/tools/ping", data={"host": "127.0.0.1", "timeout": "3"}, headers={**ORIGIN, **HX}
    )

    assert response.status_code == 500
    assert "command or path was not found" in response.text


def test_port_page_has_host_port_timeout_and_start_button(client: TestClient) -> None:
    pane = client.get("/tools/port", headers=HX).text

    assert 'hx-post="/tools/port"' in pane
    assert 'hx-target="#port-result"' in pane
    assert 'hx-sync="this:replace"' in pane
    assert 'name="host"' in pane
    assert 'name="port" type="number"' in pane
    assert 'name="timeout" type="number" step="any"' in pane
    assert 'value="3"' in pane
    assert "min=" not in pane
    assert "max=" not in pane
    assert "required" not in pane
    assert '<button type="submit">Start</button>' in pane
    assert "Enter a host and port and press Start." in pane
    assert "hx-swap-oob" not in pane


def test_port_post_renders_open_and_closed_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_open(host: str, port: int, *, timeout: float = DEFAULT_TIMEOUT) -> PortResult:
        del timeout
        return PortResult(host, port, True, 4.26, "open")

    monkeypatch.setattr(network, "check_port", fake_open)
    open_resp = client.post(
        "/tools/port",
        data={"host": " example.com ", "port": "443", "timeout": "2"},
        headers={**ORIGIN, **HX},
    )
    assert open_resp.status_code == 200
    assert "Open" in open_resp.text
    assert "4.3 ms" in open_resp.text
    assert "example.com:443 — open" in open_resp.text
    assert 'hx-swap-oob="true"' in open_resp.text

    def fake_closed(host: str, port: int, *, timeout: float = DEFAULT_TIMEOUT) -> PortResult:
        del timeout
        return PortResult(host, port, False, None, "connection refused")

    monkeypatch.setattr(network, "check_port", fake_closed)
    closed_resp = client.post(
        "/tools/port",
        data={"host": "127.0.0.1", "port": "65000"},
        headers={**ORIGIN, **HX},
    )
    assert closed_resp.status_code == 200
    assert "Closed" in closed_resp.text
    assert "—" in closed_resp.text
    assert "127.0.0.1:65000 — connection refused" in closed_resp.text


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"host": "", "port": "443", "timeout": "3"}, "Host must not be empty"),
        ({"host": "127.0.0.1", "port": "", "timeout": "3"}, "Port: enter a whole number"),
        ({"host": "127.0.0.1", "port": "abc", "timeout": "3"}, "Port: enter a whole number"),
        ({"host": "127.0.0.1", "port": "70000", "timeout": "3"}, "outside 1 to 65535"),
        ({"host": "127.0.0.1", "port": "0", "timeout": "3"}, "outside 1 to 65535"),
        (
            {"host": "127.0.0.1", "port": "443", "timeout": "0"},
            "Timeout must be a finite positive number",
        ),
        (
            {"host": "127.0.0.1", "port": "443", "timeout": "bad"},
            "Timeout: enter a number of seconds",
        ),
    ],
)
def test_port_post_invalid_input_returns_422(
    client: TestClient, data: dict[str, str], message: str
) -> None:
    response = client.post("/tools/port", data=data, headers={**ORIGIN, **HX})

    assert response.status_code == 422
    assert message in response.text
