import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pytoolbox.errors import InputError, OperationError
from pytoolbox.registry import System, Tool
from pytoolbox.results import BatchResult, Cancel, ItemFailure, LogEvent, Progress
from pytoolbox_gui.screens import Form, Fragment, Screen
from pytoolbox_gui.server import create_app
from pytoolbox_gui.sse import Stream, Value

ORIGIN = {"Origin": "http://127.0.0.1:8000"}
ECHO = Tool("echo", System.DEVELOPER, "Text", "Echo the text.")
BATCH = Tool("batch", System.OPERATIONS, "Files", "Copy files.")


def echo(form: Form) -> Fragment:
    text = form.get("text", [""])[-1]
    if text == "bad":
        msg = "text is bad"
        raise InputError(msg)
    if text == "fail":
        msg = "disk is full"
        raise OperationError(msg)
    if text == "crash":
        msg = "bug"
        raise RuntimeError(msg)
    return Fragment("echo_result.html", {"text": text})


def batch(form: Form) -> Stream:
    def stream(cancel: Cancel) -> Iterator[Value]:
        yield Progress(1, 2, Path("a.txt"))
        yield LogEvent(datetime(2026, 10, 22, tzinfo=UTC), "copied <a.txt>")
        if form.get("mode") == ["fail"]:
            msg = "target is locked"
            raise OperationError(msg)
        yield BatchResult(1, (ItemFailure(Path("b.txt"), "denied"),), cancelled=cancel())

    return stream


@pytest.fixture
def templates(tmp_path: Path) -> Path:
    (tmp_path / "echo.html").write_text("<h1>{{ tool.name }}</h1>", encoding="utf-8")
    (tmp_path / "batch.html").write_text("<h1>{{ tool.name }}</h1>", encoding="utf-8")
    (tmp_path / "echo_result.html").write_text("<output>{{ text }}</output>", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(templates: Path) -> Iterator[TestClient]:
    app = create_app(
        (ECHO, BATCH),
        (
            Screen(ECHO, "echo.html", "search", run=echo),
            Screen(BATCH, "batch.html", "home", stream=batch),
        ),
        template_dirs=(templates,),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False) as test:
        yield test


def test_home_is_the_shell_with_the_sidebar(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "/static/htmx-2.0.11.min.js" in html
    assert html.index("Developer Utilities") < html.index("System Operations")
    assert 'href="/tools/echo"' in html
    assert 'href="/tools/batch"' in html
    assert 'hx-get="/search"' in html
    assert 'id="sidebar-collapsed"' in html


def test_home_shows_a_card_per_tool_with_its_icon_and_summary(client: TestClient) -> None:
    pane = client.get("/", headers={"HX-Request": "true"}).text

    assert "<nav" not in pane
    assert pane.count('class="card"') == 2
    assert "ic_fluent_search_20_regular.svg" in pane
    assert "Echo the text." in pane
    assert pane.index("Developer Utilities") < pane.index("System Operations")


@pytest.mark.parametrize(
    ("query", "found"), [("ECHO", ["echo"]), ("copy", ["batch"]), ("", ["echo", "batch"])]
)
def test_search_matches_name_and_summary_ignoring_case(
    client: TestClient, query: str, found: list[str]
) -> None:
    pane = client.get("/search", params={"q": query}, headers={"HX-Request": "true"}).text

    assert [name for name in ("echo", "batch") if f'href="/tools/{name}"' in pane] == found


def test_search_without_a_match_says_so(client: TestClient) -> None:
    pane = client.get("/search", params={"q": "<zzz>"}, headers={"HX-Request": "true"}).text

    assert "No tools match." in pane
    assert "&lt;zzz&gt;" in pane


def test_search_without_htmx_is_the_shell_with_the_query_kept(client: TestClient) -> None:
    html = client.get("/search", params={"q": "echo"}).text

    assert "<nav" in html
    assert 'value="echo"' in html


def test_shell_icons_are_shipped_with_their_license(client: TestClient) -> None:
    for name in ("home", "navigation", "search", "chevron_down"):
        response = client.get(f"/static/icons/ic_fluent_{name}_20_regular.svg")
        assert response.status_code == 200
    assert "MIT License" in client.get("/static/icons/LICENSE").text


def test_sidebar_says_there_are_no_tools_for_an_empty_registry(templates: Path) -> None:
    app = create_app((), (), template_dirs=(templates,))
    with TestClient(app, base_url="http://127.0.0.1:8000") as test:
        html = test.get("/").text

    assert "No tools yet." in html
    assert "Developer Utilities" not in html


def test_tool_page_is_full_without_htmx_and_a_pane_with_it(client: TestClient) -> None:
    full = client.get("/tools/echo")
    pane = client.get("/tools/echo", headers={"HX-Request": "true"})

    assert "<nav" in full.text
    assert "<h1>echo</h1>" in full.text
    assert pane.text == "<h1>echo</h1>"


def test_unknown_tool_page_is_404(client: TestClient) -> None:
    response = client.get("/tools/nope", headers={"HX-Request": "true"})

    assert response.status_code == 404
    assert "Unknown tool: nope" in response.text


def test_run_returns_the_result_fragment_and_escapes_input(client: TestClient) -> None:
    response = client.post("/tools/echo", data={"text": "<b>hi</b>"}, headers=ORIGIN)

    assert response.status_code == 200
    assert response.text == "<output>&lt;b&gt;hi&lt;/b&gt;</output>"


@pytest.mark.parametrize(
    ("text", "status", "message"),
    [
        ("bad", 422, "text is bad"),
        ("fail", 500, "disk is full"),
        ("crash", 500, "Unexpected failure. See the server log."),
    ],
)
def test_run_errors_map_to_status_and_an_error_fragment(
    client: TestClient, text: str, status: int, message: str
) -> None:
    response = client.post(
        "/tools/echo", data={"text": text}, headers={**ORIGIN, "HX-Request": "true"}
    )

    assert response.status_code == status
    assert response.text == f'<p class="error" role="alert">{message}</p>'


def test_run_of_a_streaming_tool_is_422(client: TestClient) -> None:
    response = client.post("/tools/batch", headers={**ORIGIN, "HX-Request": "true"})

    assert response.status_code == 422
    assert "/tools/batch/runs" in response.text


def test_start_of_a_tool_that_does_not_stream_is_422(client: TestClient) -> None:
    response = client.post("/tools/echo/runs", headers={**ORIGIN, "HX-Request": "true"})

    assert response.status_code == 422


def _run_id(client: TestClient, data: dict[str, str] | None = None) -> str:
    response = client.post("/tools/batch/runs", data=data, headers=ORIGIN)
    assert response.status_code == 200
    marker = 'sse-connect="/runs/'
    start = response.text.index(marker) + len(marker)
    return response.text[start : response.text.index("/events", start)]


def _events(client: TestClient, run_id: str) -> list[tuple[str, str]]:
    with client.stream("GET", f"/runs/{run_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        body = "".join(response.iter_text())
    events: list[tuple[str, str]] = []
    for block in body.strip("\n").split("\n\n"):
        lines = block.split("\n")
        name = lines[0].removeprefix("event: ")
        data = "\n".join(line.removeprefix("data: ") for line in lines[1:])
        events.append((name, data))
    return events


def test_stream_sends_named_html_events_and_ends_with_done(client: TestClient) -> None:
    events = _events(client, _run_id(client))

    assert [name for name, _ in events] == ["progress", "log", "result", "done"]
    assert "1 / 2: a.txt" in events[0][1]
    assert "copied &lt;a.txt&gt;" in events[1][1]
    assert "1 succeeded, 1 failed." in events[2][1]
    assert "b.txt: denied" in events[2][1]
    assert events[3] == ("done", "done")


def test_stream_reports_an_error_event(client: TestClient) -> None:
    events = _events(client, _run_id(client, {"mode": "fail"}))

    assert [name for name, _ in events] == ["progress", "log", "error", "done"]
    assert "target is locked" in events[2][1]


def test_cancel_returns_204(client: TestClient) -> None:
    run_id = _run_id(client)

    assert client.post(f"/runs/{run_id}/cancel", headers=ORIGIN).status_code == 204


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/runs/nope/events").status_code == 404
    assert client.post("/runs/nope/cancel", headers=ORIGIN).status_code == 404


def test_stopping_the_server_cancels_every_run(templates: Path) -> None:
    seen: list[Cancel] = []

    def waiting(_form: Form) -> Stream:
        def stream(cancel: Cancel) -> Iterator[Value]:
            seen.append(cancel)
            while not cancel():
                time.sleep(0.01)
            yield BatchResult(0, cancelled=True)

        return stream

    app = create_app(
        (BATCH,), (Screen(BATCH, "batch.html", "home", stream=waiting),), template_dirs=(templates,)
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as test:
        test.post("/tools/batch/runs", headers=ORIGIN)
        while not seen:
            time.sleep(0.01)
        assert not seen[0]()

    assert seen[0]()


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1:9000", "localhost"])
def test_a_foreign_host_is_403(client: TestClient, host: str) -> None:
    response = client.get("/", headers={"Host": host})

    assert response.status_code == 403


def test_localhost_is_an_allowed_host(client: TestClient) -> None:
    response = client.post(
        "/tools/echo",
        data={"text": "x"},
        headers={"Host": "localhost:8000", "Origin": "http://localhost:8000"},
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "http://evil.example"},
        {"Origin": "http://127.0.0.1:9000"},
    ],
)
def test_a_post_without_a_loopback_origin_is_403(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.post("/tools/echo", data={"text": "x"}, headers=headers)

    assert response.status_code == 403


def test_static_assets_are_served(client: TestClient) -> None:
    for name in ("app.css", "htmx-2.0.11.min.js", "htmx-ext-sse-2.2.4.min.js"):
        assert client.get(f"/static/{name}").status_code == 200


def test_the_default_app_has_no_tools() -> None:
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as test:
        assert "No tools yet." in test.get("/").text


def test_a_tool_without_a_screen_is_refused() -> None:
    with pytest.raises(ValueError, match="Tools without a screen: echo"):
        create_app((ECHO,), ())


def test_a_screen_without_a_tool_is_refused() -> None:
    with pytest.raises(ValueError, match="Screens without a tool: echo"):
        create_app((), (Screen(ECHO, "echo.html", "home"),))


def test_two_screens_for_one_tool_are_refused() -> None:
    with pytest.raises(ValueError, match="Two screens share a tool name"):
        create_app((ECHO,), (Screen(ECHO, "a.html", "home"), Screen(ECHO, "b.html", "home")))


def test_a_screen_whose_icon_is_not_shipped_is_refused() -> None:
    with pytest.raises(ValueError, match="Icons not in static/icons: no_such_icon"):
        create_app((ECHO,), (Screen(ECHO, "echo.html", "no_such_icon"),))
