from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from pytoolbox.db import HistoryStore
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, ItemFailure, Progress
from pytoolbox.tools.system.file_copy import DEFAULT_FORMAT, FileCopyResult
from pytoolbox_gui import screens
from pytoolbox_gui.screens import file_copy_report, stream_file_copy
from pytoolbox_gui.server import create_app
from pytoolbox_gui.sse import Report

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture
def env_state(tmp_path: Path) -> tuple[Path, HistoryStore]:
    state = tmp_path / "state"
    return state, HistoryStore(state_root=state)


@pytest.fixture
def client(env_state: tuple[Path, HistoryStore]) -> Iterator[TestClient]:
    _state, store = env_state
    with TestClient(create_app(history=store), base_url="http://127.0.0.1:8000") as test:
        yield test


def _start(
    client: TestClient,
    *,
    source: str,
    dest_dir: str,
    fmt: str = DEFAULT_FORMAT,
    start: str = "1",
    count: str = "2",
    dry_run: bool = False,
    overwrite: bool = False,
) -> tuple[int, str]:
    data: dict[str, str] = {
        "source": source,
        "dest_dir": dest_dir,
        "format": fmt,
        "start": start,
        "count": count,
    }
    if dry_run:
        data["dry_run"] = "on"
    if overwrite:
        data["overwrite"] = "on"
    response = client.post("/tools/file-copy/runs", data=data, headers={**ORIGIN, **HX})
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


def test_page_shows_defaults_and_icon(client: TestClient) -> None:
    assert client.get("/static/icons/ic_fluent_document_copy_20_regular.svg").status_code == 200
    pane = client.get("/tools/file-copy", headers=HX).text

    assert 'hx-post="/tools/file-copy/runs"' in pane
    assert 'hx-target="#file-copy-run"' in pane
    assert f'value="{DEFAULT_FORMAT}"' in pane
    assert 'name="start"' in pane
    assert 'value="1"' in pane
    assert 'name="count"' in pane
    assert 'value="3"' in pane
    assert 'name="dry_run"' in pane
    assert 'name="overwrite"' in pane
    assert '<button type="submit">Start</button>' in pane


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"source": ""}, "Source file: enter a path."),
        ({"source": "relative.txt"}, "Source file: use an absolute path"),
        ({"dest_dir": ""}, "Destination folder: enter a path."),
        ({"dest_dir": "relative_dir"}, "Destination folder: use an absolute path"),
        ({"start": "nope"}, "Start: enter a whole number"),
        ({"count": "nope"}, "Count: enter a whole number"),
        ({"count": "0"}, "Count must be at least 1"),
        ({"fmt": ""}, "Format must not be empty"),
    ],
)
def test_bad_input_is_422_before_the_run_starts(
    client: TestClient, tmp_path: Path, overrides: dict[str, str], message: str
) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data", encoding="utf-8")
    dest = tmp_path / "out"

    params = {
        "source": str(src),
        "dest_dir": str(dest),
        "fmt": DEFAULT_FORMAT,
        "start": "1",
        "count": "2",
    } | overrides
    status, html = _start(
        client,
        source=params["source"],
        dest_dir=params["dest_dir"],
        fmt=params["fmt"],
        start=params["start"],
        count=params["count"],
    )

    assert status == 422
    assert message in html


def test_dry_run_copy_overwrite_and_history(
    client: TestClient, tmp_path: Path, env_state: tuple[Path, HistoryStore]
) -> None:
    _state, store = env_state
    src = tmp_path / "sample.txt"
    src.write_text("v1", encoding="utf-8")
    dest = tmp_path / "out"

    # Dry-run with quoted Explorer-style path does not write or record history.
    status, html = _start(
        client,
        source=f'"{src}"',
        dest_dir=f'"{dest}"',
        count="2",
        dry_run=True,
    )
    assert status == 200
    dry_events = _events(client, html)
    assert [name for name, _ in dry_events] == ["progress", "progress", "result", "done"]
    dry_result = dry_events[2][1]
    assert "Dry-run: 2 files planned." in dry_result
    assert str(dest.resolve() / "sample_001.txt") in dry_result
    assert not dest.exists()
    assert store.list_runs() == ()

    # Real copy creates files and records history.
    status, html = _start(client, source=str(src), dest_dir=str(dest), count="2")
    assert status == 200
    copy_events = _events(client, html)
    assert [name for name, _ in copy_events] == ["progress", "progress", "result", "done"]
    copy_result = copy_events[2][1]
    assert "Copied: 2 files written, 0 failed." in copy_result
    assert (dest / "sample_001.txt").read_text(encoding="utf-8") == "v1"
    assert (dest / "sample_002.txt").read_text(encoding="utf-8") == "v1"

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "file-copy"
    assert runs[0].succeeded == 2
    assert runs[0].status == "succeeded"

    history_pane = client.get("/history", headers=HX).text
    assert "<code>file-copy</code>" in history_pane
    assert "Succeeded" in history_pane

    # Re-running without overwrite returns 422 before starting.
    conflict_status, conflict_html = _start(client, source=str(src), dest_dir=str(dest), count="2")
    assert conflict_status == 422
    assert "already exists" in conflict_html

    # Re-running with overwrite replaces files and records another history row.
    src.write_text("v2", encoding="utf-8")
    status, html = _start(client, source=str(src), dest_dir=str(dest), count="2", overwrite=True)
    assert status == 200
    assert 'id="error"' in html
    overwrite_events = _events(client, html)
    assert [name for name, _ in overwrite_events] == ["progress", "progress", "result", "done"]
    assert (dest / "sample_001.txt").read_text(encoding="utf-8") == "v2"
    assert len(store.list_runs()) == 2


def test_lock_conflict_streams_error_event_and_records_history(
    client: TestClient, tmp_path: Path, env_state: tuple[Path, HistoryStore]
) -> None:
    state, store = env_state
    src = tmp_path / "src.txt"
    src.write_text("abc", encoding="utf-8")
    dest = tmp_path / "out"
    locked = dest.resolve() / "src_001.txt"

    with target_lock(locked, state_root=state):
        status, html = _start(client, source=str(src), dest_dir=str(dest), count="1")
        assert status == 200
        events = _events(client, html)

    assert [name for name, _ in events] == ["error", "done"]
    assert "Target is locked by another run" in events[0][1]
    assert not (dest / "src_001.txt").exists()
    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"


def test_report_caps_rows_defaults_optional_fields_and_cancel_stops_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env_state: tuple[Path, HistoryStore]
) -> None:
    state, _store = env_state
    src = tmp_path / "a.txt"
    src.write_text("aa", encoding="utf-8")
    dest = tmp_path / "out"
    done = 0

    def cancel() -> bool:
        return done >= 1

    # Omitting optional format/start falls back to defaults.
    stream = stream_file_copy(
        {"source": [str(src)], "dest_dir": [str(dest)], "count": ["3"]},
        state_root=state,
    )
    events: list[object] = []
    for value in stream(cancel):
        events.append(value)
        if isinstance(value, Progress):
            done += 1

    last = events[-1]
    assert isinstance(last, Report)
    result = cast("FileCopyResult", last.context["result"])
    assert result.batch.cancelled is True
    assert result.batch.succeeded == 1

    monkeypatch.setattr(screens, "SHOWN_ITEMS", 1)
    capped = file_copy_report(
        FileCopyResult(
            source=src,
            dest_dir=dest,
            items=(dest / "a_001.txt", dest / "a_002.txt"),
            dry_run=False,
            batch=BatchResult(
                2, (ItemFailure(dest / "a_003.txt", "access is denied"),), cancelled=True
            ),
        )
    )
    rows = cast("tuple[Path, ...]", capped.context["rows"])
    assert len(rows) == 1
