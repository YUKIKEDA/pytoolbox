from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pytoolbox.db import HistoryStore
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, ItemFailure, Progress
from pytoolbox.tools.dev.mock_image import MockImageItem, MockImageResult
from pytoolbox_gui.screens import mock_image_report
from pytoolbox_gui.server import create_app

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


def _start(
    client: TestClient,
    output_dir: str,
    *,
    width: str = "120",
    height: str = "80",
    count: str = "2",
    image_format: str = "png",
    seed: str = "",
    random_ratio: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
) -> tuple[int, str]:
    data: dict[str, str] = {
        "output_dir": output_dir,
        "width": width,
        "height": height,
        "count": count,
        "format": image_format,
        "seed": seed,
    }
    if random_ratio:
        data["random_ratio"] = "on"
    if dry_run:
        data["dry_run"] = "on"
    if overwrite:
        data["overwrite"] = "on"
    response = client.post("/tools/mock-image/runs", data=data, headers={**ORIGIN, **HX})
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


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, HistoryStore]]:
    store = HistoryStore(state_root=tmp_path / "state")
    with TestClient(
        create_app(history=store), base_url="http://127.0.0.1:8000", raise_server_exceptions=False
    ) as test:
        yield test, store


def test_mock_image_screen_dry_run_generate_overwrite_and_history(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, store = client
    assert test.get("/static/icons/ic_fluent_image_20_regular.svg").status_code == 200

    page = test.get("/tools/mock-image", headers=HX).text
    assert 'hx-post="/tools/mock-image/runs"' in page
    assert 'name="random_ratio"' in page

    out_dir = tmp_path / "images"
    status, dry_run_html = _start(test, f'"{out_dir}"', dry_run=True, random_ratio=True, seed="11")
    assert status == 200
    dry_events = _events(test, dry_run_html)
    assert [name for name, _ in dry_events if name == "progress"]
    dry_result = dict(dry_events)["result"]
    assert "Dry-run in" in dry_result
    assert "2 PNG images planned" in dry_result
    assert not out_dir.exists()
    assert store.list_runs() == ()

    status, write_html = _start(
        test, str(out_dir), width="100", height="60", count="2", image_format="jpg", seed="11"
    )
    assert status == 200
    write_events = _events(test, write_html)
    write_result = dict(write_events)["result"]
    assert "Generated in" in write_result
    assert "2 JPG images" in write_result
    assert (out_dir / "mock_001.jpg").is_file()
    with Image.open(out_dir / "mock_001.jpg") as opened:
        assert opened.format == "JPEG"
        assert opened.size == (100, 60)

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "mock-image"
    history_pane = test.get("/history", headers=HX).text
    assert "mock-image" in history_pane

    status_conflict, conflict_html = _start(test, str(out_dir), image_format="jpg")
    assert status_conflict == 422
    assert "already exists" in conflict_html

    status_over, over_html = _start(
        test, str(out_dir), width="90", height="50", image_format="jpg", overwrite=True
    )
    assert status_over == 200
    _events(test, over_html)
    with Image.open(out_dir / "mock_001.jpg") as opened:
        assert opened.size == (90, 50)
    assert len(store.list_runs()) == 2


def test_mock_image_screen_validation_and_lock_error(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, store = client
    state = store.path.parent

    for data, expected in [
        ({"output_dir": ""}, "Output folder: enter a path"),
        ({"output_dir": "relative/dir"}, "use an absolute path"),
        ({"output_dir": str(tmp_path / "a"), "width": "abc"}, "Width: enter a whole number"),
        ({"output_dir": str(tmp_path / "a"), "width": "0"}, "Width must be at least 1"),
        ({"output_dir": str(tmp_path / "a"), "seed": "bad"}, "Seed: enter a whole number"),
        ({"output_dir": str(tmp_path / "a"), "format": "bmp"}, "Unknown image format"),
    ]:
        payload = {
            "output_dir": data["output_dir"],
            "width": data.get("width", "120"),
            "height": data.get("height", "80"),
            "count": data.get("count", "2"),
            "format": data.get("format", "png"),
            "seed": data.get("seed", ""),
        }
        response = test.post("/tools/mock-image/runs", data=payload, headers={**ORIGIN, **HX})
        assert response.status_code == 422
        assert expected in response.text

    locked_dir = tmp_path / "locked_dir"
    with target_lock(locked_dir.resolve() / "mock_001.png", state_root=state):
        status, run_html = _start(test, str(locked_dir), count="1")
        assert status == 200
        events = dict(_events(test, run_html))
        assert "error" in events
        assert "Target is locked" in events["error"]


def test_mock_image_report_truncates_over_1000_and_renders_failures(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, _store = client
    items = tuple(
        MockImageItem(tmp_path / f"mock_{i:04d}.png", 10, 10, 123) for i in range(1, 1005)
    )
    result = MockImageResult(
        output_dir=tmp_path,
        format="png",
        items=items,
        seed=None,
        random_ratio=False,
        dry_run=False,
        batch=BatchResult(1004, (ItemFailure(tmp_path / "bad.png", "access is denied"),), True),
    )
    report = mock_image_report(result)
    rows = cast("list[tuple[MockImageItem, str]]", report.context["rows"])
    assert len(rows) == 1000
    assert isinstance(Progress(1, 1, tmp_path), Progress)
    assert test.get("/tools/mock-image").status_code == 200
