from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from pytoolbox.db import HistoryStore
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, ItemFailure, Progress
from pytoolbox.tools.system import file_merge
from pytoolbox.tools.system.file_merge import DEFAULT_SEPARATOR, FileMergeResult
from pytoolbox_gui.screens import file_merge_report
from pytoolbox_gui.server import create_app

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


def _fixed_now() -> datetime:
    return datetime(2026, 11, 23, 12, 34, 56, tzinfo=UTC)


def _start(
    client: TestClient,
    inputs: str,
    output_dir: str,
    *,
    sep: str = DEFAULT_SEPARATOR,
    dry_run: bool = False,
    overwrite: bool = False,
) -> tuple[int, str]:
    data: dict[str, str] = {
        "inputs": inputs,
        "output_dir": output_dir,
        "sep": sep,
    }
    if dry_run:
        data["dry_run"] = "on"
    if overwrite:
        data["overwrite"] = "on"
    response = client.post("/tools/file-merge/runs", data=data, headers={**ORIGIN, **HX})
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


def test_file_merge_screen_dry_run_merge_unreadable_overwrite_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: tuple[TestClient, HistoryStore],
) -> None:
    test, store = client
    monkeypatch.setattr(file_merge, "_utc_now", _fixed_now)
    assert test.get("/static/icons/ic_fluent_merge_20_regular.svg").status_code == 200

    page = test.get("/tools/file-merge", headers=HX).text
    assert 'hx-post="/tools/file-merge/runs"' in page
    assert 'name="inputs"' in page
    assert DEFAULT_SEPARATOR in page

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    missing = tmp_path / "missing.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    out_dir = tmp_path / "out"
    inputs_text = f'\n  "{a}" \r\n\r\n{b}\n{missing}\n'

    status, dry_html = _start(test, inputs_text, f'"{out_dir}"', dry_run=True)
    assert status == 200
    dry_events = _events(test, dry_html)
    assert [name for name, _ in dry_events if name == "progress"]
    dry_result = dict(dry_events)["result"]
    assert "Dry-run for" in dry_result
    assert "2 readable inputs, 1 failed" in dry_result
    assert "path was not found" in dry_result
    assert not out_dir.exists()
    assert store.list_runs() == ()

    status, write_html = _start(test, inputs_text, str(out_dir), sep="")
    assert status == 200
    write_events = _events(test, write_html)
    write_result = dict(write_events)["result"]
    assert "Wrote" in write_result
    assert "2 merged, 1 failed" in write_result

    stamp = _fixed_now().astimezone().strftime("%Y%m%d_%H%M%S")
    merged_file = out_dir / f"merged_{stamp}.txt"
    assert merged_file.is_file()
    content = merged_file.read_text(encoding="utf-8")
    assert "--- a.txt ---\nalpha\n" in content
    assert "--- b.txt ---\nbeta\n" in content
    assert "--- missing.txt ---\n[unreadable: path was not found]\n" in content

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "file-merge"
    assert runs[0].status == "partial"
    history_pane = test.get("/history", headers=HX).text
    assert "file-merge" in history_pane

    status_conflict, conflict_html = _start(test, str(a), str(out_dir))
    assert status_conflict == 422
    assert "already exists" in conflict_html

    status_over, over_html = _start(
        test, str(a), str(out_dir), sep="=== {name} ===", overwrite=True
    )
    assert status_over == 200
    _events(test, over_html)
    assert merged_file.read_text(encoding="utf-8") == "=== a.txt ===\nalpha\n"
    assert len(store.list_runs()) == 2


def test_file_merge_screen_validation_and_lock_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: tuple[TestClient, HistoryStore],
) -> None:
    test, store = client
    state = store.path.parent
    monkeypatch.setattr(file_merge, "_utc_now", _fixed_now)
    a = tmp_path / "a.txt"
    a.write_text("alpha", encoding="utf-8")

    for data, expected in [
        (
            {"inputs": "   \n  ", "output_dir": str(tmp_path / "out")},
            "Input files: enter at least one path",
        ),
        (
            {"inputs": "relative/file.txt", "output_dir": str(tmp_path / "out")},
            "Input files: use an absolute path",
        ),
        ({"inputs": str(a), "output_dir": ""}, "Output folder: enter a path"),
        ({"inputs": str(a), "output_dir": "relative/out"}, "Output folder: use an absolute path"),
        (
            {"inputs": str(a), "output_dir": str(tmp_path / "out"), "sep": "{bad_key}"},
            "Invalid separator format",
        ),
    ]:
        payload = {
            "inputs": data["inputs"],
            "output_dir": data["output_dir"],
            "sep": data.get("sep", DEFAULT_SEPARATOR),
        }
        response = test.post("/tools/file-merge/runs", data=payload, headers={**ORIGIN, **HX})
        assert response.status_code == 422
        assert expected in response.text

    locked_dir = tmp_path / "locked_dir"
    stamp = _fixed_now().astimezone().strftime("%Y%m%d_%H%M%S")
    with target_lock(locked_dir.resolve() / f"merged_{stamp}.txt", state_root=state):
        status, run_html = _start(test, str(a), str(locked_dir))
        assert status == 200
        events = dict(_events(test, run_html))
        assert "error" in events
        assert "Target is locked" in events["error"]


def test_file_merge_report_truncates_over_1000_and_renders_full_page(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, _store = client
    inputs = tuple(tmp_path / f"f_{i:04d}.txt" for i in range(1, 1005))
    result = FileMergeResult(
        inputs=inputs,
        output=tmp_path / "merged_20261123_123456.txt",
        dry_run=False,
        batch=BatchResult(1003, (ItemFailure(inputs[0], "access is denied"),), True),
    )
    report = file_merge_report(result)
    rows = cast("list[tuple[Path, str | None]]", report.context["rows"])
    assert len(rows) == 1000
    assert rows[0] == (inputs[0], "access is denied")
    assert isinstance(Progress(1, 1, tmp_path), Progress)
    assert test.get("/tools/file-merge").status_code == 200
