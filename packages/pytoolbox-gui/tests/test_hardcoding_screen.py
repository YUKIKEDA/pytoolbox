from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pytoolbox.results import Progress
from pytoolbox.tools.dev.hardcoding import HardcodingResult
from pytoolbox_gui import screens
from pytoolbox_gui.screens import stream_hardcoding
from pytoolbox_gui.server import create_app
from pytoolbox_gui.sse import Report

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}
KINDS = ["string", "number", "ip", "url", "email"]


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as test:
        yield test


def _form(folder: Path | str, **fields: list[str]) -> dict[str, list[str]]:
    return {"folder": [str(folder)], "extensions": [".py"], "exclude": [""], "kind": KINDS} | fields


def _start(client: TestClient, folder: Path | str, kinds: list[str] = KINDS) -> tuple[int, str]:
    data: dict[str, str | list[str]] = {
        "folder": str(folder),
        "extensions": ".py",
        "exclude": "",
        "kind": kinds,
    }
    response = client.post("/tools/hardcoding/runs", data=data, headers={**ORIGIN, **HX})
    return response.status_code, response.text


def _events(client: TestClient, html: str) -> list[tuple[str, str]]:
    marker = 'sse-connect="/runs/'
    start = html.index(marker) + len(marker)
    run_id = html[start : html.index("/events", start)]
    events: list[tuple[str, str]] = []
    with client.stream("GET", f"/runs/{run_id}/events") as response:
        name = ""
        data: list[str] = []
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data.append(line.removeprefix("data: "))
            elif not line and name:
                events.append((name, "\n".join(data)))
                name, data = "", []
    return events


def test_page_starts_on_submit_with_the_defaults_filled_in(client: TestClient) -> None:
    pane = client.get("/tools/hardcoding", headers=HX).text

    assert 'hx-post="/tools/hardcoding/runs"' in pane
    assert "hx-trigger" not in pane
    assert '<button type="submit">Start</button>' in pane
    assert 'value=".cs, .vb, .js, .py"' in pane
    assert 'value="node_modules, bin, obj, .git, .venv"' in pane
    for kind in KINDS:
        assert f'name="kind" value="{kind}" checked' in pane


def test_a_run_streams_progress_then_the_counts_and_the_table(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "a.py").write_text('host = "<b>10.0.0.1</b>"\n', "utf-8")
    (tmp_path / "b.py").write_bytes(b"\x81\x7f\xff")

    status, html = _start(client, tmp_path, ["ip"])

    assert status == 200
    assert 'id="error"' in html
    assert html.count('hx-target="this"') == 3
    events = _events(client, html)
    assert [name for name, _ in events] == ["progress", "progress", "result", "done"]
    result = events[2][1]
    assert "2 files scanned, 1 findings." in result
    assert "<dt>ip</dt><dd>1</dd>" in result
    assert "<td><code>a.py</code></td>" in result
    assert "Skipped (1)" in result
    assert "<code>b.py</code>: Not UTF-8 or CP932." in result


def test_a_run_lists_the_first_findings_and_counts_them_all(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screens, "SHOWN_FINDINGS", 2)
    (tmp_path / "a.py").write_text("1111 2222 3333\n", "utf-8")

    _, html = _start(client, tmp_path, ["number"])
    result = _events(client, html)[-2][1]

    assert "Showing 2 of 3 findings." in result
    assert result.count("<tr>") == 3


def test_a_run_without_findings_says_so(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", "utf-8")

    _, html = _start(client, tmp_path)

    assert "No findings." in _events(client, html)[-2][1]


@pytest.mark.parametrize(
    ("folder", "kinds", "message"),
    [
        ("", KINDS, "Folder: enter a path."),
        ("relative", KINDS, "Folder: use an absolute path"),
        ("missing", KINDS, "no such folder"),
        ("tmp", [], "Choose at least one kind."),
    ],
)
def test_bad_input_is_422_before_the_run_starts(
    client: TestClient, tmp_path: Path, folder: str, kinds: list[str], message: str
) -> None:
    given = {"missing": str(tmp_path / "missing"), "tmp": str(tmp_path)}.get(folder, folder)

    status, html = _start(client, given, kinds)

    assert status == 422
    assert message in html


def test_a_new_scan_stops_the_one_before(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1234\n", "utf-8")
    (tmp_path / "b.py").write_text("y = 5678\n", "utf-8")
    first = stream_hardcoding(_form(tmp_path))(lambda: False)
    assert isinstance(next(first), Progress)

    stream_hardcoding(_form(tmp_path))
    last = list(first)[-1]

    assert isinstance(last, Report)
    result = last.context["result"]
    assert isinstance(result, HardcodingResult)
    assert result.cancelled
