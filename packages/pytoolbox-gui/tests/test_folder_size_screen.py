import re
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from pytoolbox.results import Progress
from pytoolbox.tools.system.folder_size import FolderSizeResult, ItemKind, SizeItem
from pytoolbox_gui import screens
from pytoolbox_gui.screens import SizeRow, size_report, stream_folder_size
from pytoolbox_gui.server import create_app
from pytoolbox_gui.sse import Report

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as test:
        yield test


def _start(client: TestClient, folder: Path | str) -> tuple[int, str]:
    response = client.post(
        "/tools/folder-size/runs", data={"folder": str(folder)}, headers={**ORIGIN, **HX}
    )
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


def _tree(root: Path) -> Path:
    (root / "big").mkdir()
    (root / "big" / "a.bin").write_bytes(b"x" * 3072)
    (root / "a&b.txt").write_bytes(b"x" * 1024)
    return root


def test_page_starts_on_submit(client: TestClient) -> None:
    pane = client.get("/tools/folder-size", headers=HX).text

    assert 'hx-post="/tools/folder-size/runs"' in pane
    assert 'hx-target="#folder-size-run"' in pane
    assert "hx-trigger" not in pane
    assert '<button type="submit">Start</button>' in pane


def test_a_run_streams_progress_then_the_table_with_shares_and_the_csv(
    client: TestClient, tmp_path: Path
) -> None:
    status, html = _start(client, _tree(tmp_path))

    assert status == 200
    events = _events(client, html)
    assert [name for name, _ in events] == ["progress", "result", "done"]
    result = events[1][1]
    assert "4.0 KB in 2 items." in result
    assert "<td><code>big</code></td>" in result
    assert "a&amp;b.txt" in result
    assert "--share: 75.00%" in result
    assert "25.0%" in result
    href = re.search(
        r'download="folder-size.csv" href="data:text/csv;charset=utf-8,([^"]*)"', result
    )
    assert href is not None
    assert unquote(href.group(1)).startswith("﻿name,kind,bytes,size\r\nbig,folder,3072,")


def test_an_empty_folder_says_so(client: TestClient, tmp_path: Path) -> None:
    _, html = _start(client, tmp_path)

    result = _events(client, html)[-2][1]
    assert "0 B in 0 items." in result
    assert "The folder is empty." in result


def test_the_table_lists_the_first_rows_and_the_csv_has_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(screens, "SHOWN_ITEMS", 1)
    items = (SizeItem("a", ItemKind.FILE, 2), SizeItem("b", ItemKind.FILE, 1))

    report = size_report(FolderSizeResult(items, unreadable=3, cancelled=True))

    rows = cast("list[SizeRow]", report.context["rows"])
    assert [row.item.name for row in rows] == ["a"]
    assert "b,file,1" in unquote(str(report.context["csv_href"]))


@pytest.mark.parametrize(
    ("folder", "message"),
    [
        ("", "Folder: enter a path."),
        ("relative", "Folder: use an absolute path"),
        ("missing", "no such folder"),
    ],
)
def test_bad_input_is_422_before_the_run_starts(
    client: TestClient, tmp_path: Path, folder: str, message: str
) -> None:
    given = str(tmp_path / "missing") if folder == "missing" else folder

    status, html = _start(client, given)

    assert status == 422
    assert message in html


def test_a_new_walk_stops_the_one_before(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f.bin").write_bytes(b"x")
    first = stream_folder_size({"folder": [str(tmp_path)]})(lambda: False)
    assert isinstance(next(first), Progress)

    stream_folder_size({"folder": [str(tmp_path)]})
    last = list(first)[-1]

    assert isinstance(last, Report)
    result = last.context["result"]
    assert isinstance(result, FolderSizeResult)
    assert result.cancelled
