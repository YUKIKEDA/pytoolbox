from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pytoolbox.db import HistoryStore
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, ItemFailure
from pytoolbox.tools.system.image_clip import ClipBox, ClippedImageItem, ImageClipResult
from pytoolbox_gui.screens import image_clip_report
from pytoolbox_gui.server import create_app

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


def _start(
    client: TestClient,
    input_dir: str,
    *,
    output_dir: str = "",
    x1: str = "5",
    y1: str = "5",
    x2: str = "45",
    y2: str = "35",
    box: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
) -> tuple[int, str]:
    data: dict[str, str] = {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
    }
    if box:
        data["box"] = box
    if dry_run:
        data["dry_run"] = "on"
    if overwrite:
        data["overwrite"] = "on"
    response = client.post("/tools/image-clip/runs", data=data, headers={**ORIGIN, **HX})
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


def test_image_clip_screen_preview_dry_run_crop_overwrite_and_history(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, store = client
    assert test.get("/static/icons/ic_fluent_crop_20_regular.svg").status_code == 200

    page = test.get("/tools/image-clip", headers=HX).text
    assert 'hx-post="/tools/image-clip/runs"' in page
    assert 'hx-post="/tools/image-clip"' in page

    in_dir = tmp_path / "photos"
    in_dir.mkdir()
    Image.new("RGB", (120, 90), color=(40, 100, 160)).save(in_dir / "first.png", format="PNG")
    Image.new("RGB", (80, 60), color=(160, 100, 40)).save(in_dir / "second.jpg", format="JPEG")

    preview_res = test.post(
        "/tools/image-clip",
        data={"input_dir": f'"{in_dir}"'},
        headers={**ORIGIN, **HX},
    )
    assert preview_res.status_code == 200
    assert "first.png" in preview_res.text
    assert 'data-natural-width="120"' in preview_res.text
    assert 'data-natural-height="90"' in preview_res.text
    assert "data:image/png;base64," in preview_res.text

    status, dry_html = _start(test, f'"{in_dir}"', dry_run=True)
    assert status == 200
    dry_events = _events(test, dry_html)
    assert [name for name, _ in dry_events if name == "progress"]
    dry_result = dict(dry_events)["result"]
    assert "Dry-run in" in dry_result
    assert "2 images planned" in dry_result
    assert not (in_dir / "cropped_first.png").exists()
    assert store.list_runs() == ()

    status, crop_html = _start(test, str(in_dir), x1="10", y1="10", x2="60", y2="50")
    assert status == 200
    crop_events = _events(test, crop_html)
    crop_result = dict(crop_events)["result"]
    assert "Cropped in" in crop_result
    assert "2 images written" in crop_result
    assert (in_dir / "cropped_first.png").is_file()
    with Image.open(in_dir / "cropped_first.png") as opened:
        assert opened.size == (50, 40)

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "image-clip"
    history_pane = test.get("/history", headers=HX).text
    assert "image-clip" in history_pane

    status_conflict, conflict_html = _start(test, str(in_dir))
    assert status_conflict == 422
    assert "already exists" in conflict_html

    status_over, over_html = _start(test, str(in_dir), box="0,0,30,25", overwrite=True)
    assert status_over == 200
    _events(test, over_html)
    with Image.open(in_dir / "cropped_first.png") as opened:
        assert opened.size == (30, 25)
    assert len(store.list_runs()) == 2


def test_image_clip_screen_validation_and_lock_error(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, store = client
    state = store.path.parent
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    Image.new("RGB", (60, 60)).save(in_dir / "a.png", format="PNG")

    preview_empty = test.post("/tools/image-clip", data={"input_dir": ""}, headers={**ORIGIN, **HX})
    assert preview_empty.status_code == 422
    assert "Input folder: enter a path" in preview_empty.text

    for data, expected in [
        ({"input_dir": ""}, "Input folder: enter a path"),
        ({"input_dir": "relative/dir"}, "use an absolute path"),
        (
            {"input_dir": str(in_dir), "output_dir": "rel/out"},
            "Output folder: use an absolute path",
        ),
        ({"input_dir": str(in_dir), "x1": "abc"}, "X1: enter a whole number"),
        ({"input_dir": str(in_dir), "x1": "50", "x2": "10"}, "Crop box must have x1"),
    ]:
        payload = {
            "input_dir": data["input_dir"],
            "output_dir": data.get("output_dir", ""),
            "x1": data.get("x1", "0"),
            "y1": data.get("y1", "0"),
            "x2": data.get("x2", "20"),
            "y2": data.get("y2", "20"),
        }
        response = test.post("/tools/image-clip/runs", data=payload, headers={**ORIGIN, **HX})
        assert response.status_code == 422
        assert expected in response.text

    locked_out = tmp_path / "locked_out"
    with target_lock(locked_out.resolve() / "cropped_a.png", state_root=state):
        status, run_html = _start(test, str(in_dir), output_dir=str(locked_out))
        assert status == 200
        events = dict(_events(test, run_html))
        assert "error" in events
        assert "Target is locked" in events["error"]


def test_image_clip_report_truncates_over_1000(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, _store = client
    items = tuple(
        ClippedImageItem(
            tmp_path / f"img_{i:04d}.png",
            tmp_path / f"cropped_img_{i:04d}.png",
            ClipBox(0, 0, 10, 10),
        )
        for i in range(1, 1005)
    )
    result = ImageClipResult(
        input_dir=tmp_path,
        output_dir=tmp_path,
        box=ClipBox(0, 0, 10, 10),
        items=items,
        dry_run=False,
        batch=BatchResult(1004, (ItemFailure(tmp_path / "bad.png", "access is denied"),), True),
    )
    report = image_clip_report(result)
    rows = cast("tuple[ClippedImageItem, ...]", report.context["rows"])
    assert len(rows) == 1000
    assert test.get("/tools/image-clip").status_code == 200
