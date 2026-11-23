from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from pytoolbox.db import HistoryStore
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, ItemFailure, Progress
from pytoolbox.tools.system.folder_copy import DEFAULT_FORMAT, FolderCopyResult
from pytoolbox_gui import screens
from pytoolbox_gui.screens import folder_copy_report, stream_folder_copy
from pytoolbox_gui.server import create_app
from pytoolbox_gui.sse import Report

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture
def env_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, HistoryStore]:
    state = tmp_path / "state"
    monkeypatch.setenv("LOCALAPPDATA", str(state))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
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
    overwrite: bool = False,
    dry_run: bool = False,
) -> tuple[int, str]:
    data: dict[str, str] = {
        "source": source,
        "dest_dir": dest_dir,
        "format": fmt,
        "start": start,
        "count": count,
    }
    if overwrite:
        data["overwrite"] = "on"
    if dry_run:
        data["dry_run"] = "on"
    response = client.post("/tools/folder-copy/runs", data=data, headers={**ORIGIN, **HX})
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


def _seed_source(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "sample_src"
    (src / "sub").mkdir(parents=True)
    (src / "hello.txt").write_text("world", encoding="utf-8")
    (src / "sub" / "item.txt").write_text("nested", encoding="utf-8")
    dest = tmp_path / "dest"
    return src, dest


def test_page_renders_expected_form_defaults(client: TestClient) -> None:
    pane = client.get("/tools/folder-copy", headers=HX).text

    assert 'hx-post="/tools/folder-copy/runs"' in pane
    assert 'hx-target="#folder-copy-run"' in pane
    assert 'name="source"' in pane
    assert 'name="dest_dir"' in pane
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
        ({"source": ""}, "Source folder: enter a path."),
        ({"dest_dir": ""}, "Destination folder: enter a path."),
        ({"source": "relative_src"}, "Source folder: use an absolute path"),
        ({"dest_dir": "relative_dest"}, "Destination folder: use an absolute path"),
        ({"start": "abc"}, "Start number: enter a whole number"),
        ({"count": "abc"}, "Count: enter a whole number"),
        ({"count": "0"}, "Count must be at least 1"),
        ({"fmt": ""}, "Folder name format must not be empty"),
    ],
)
def test_bad_inputs_return_422_before_stream_starts(
    client: TestClient,
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    src, dest = _seed_source(tmp_path)
    params = {
        "source": str(src),
        "dest_dir": str(dest),
        "fmt": DEFAULT_FORMAT,
        "start": "1",
        "count": "2",
    }
    params.update(overrides)

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


def test_dry_run_streams_progress_and_leaves_disk_and_history_untouched(
    client: TestClient, tmp_path: Path, env_state: tuple[Path, HistoryStore]
) -> None:
    _state, store = env_state
    src, dest = _seed_source(tmp_path)

    status, html = _start(
        client,
        source=f'"{src}"',
        dest_dir=f'"{dest}"',
        count="2",
        dry_run=True,
    )

    assert status == 200
    events = _events(client, html)
    assert [name for name, _ in events] == ["progress", "progress", "result", "done"]
    result_html = events[2][1]
    assert "Dry-run: 2 folders planned." in result_html
    assert str((dest / "sample_src_001").resolve()) in result_html
    assert str((dest / "sample_src_002").resolve()) in result_html
    assert not dest.exists()
    assert not store.path.exists()


def test_apply_copies_folders_handles_overwrite_and_records_history(
    client: TestClient, tmp_path: Path, env_state: tuple[Path, HistoryStore]
) -> None:
    _state, store = env_state
    src, dest = _seed_source(tmp_path)

    status, html = _start(
        client,
        source=str(src),
        dest_dir=str(dest),
        fmt="{name}_{n:02d}",
        start="2",
        count="2",
    )

    assert status == 200
    events = _events(client, html)
    assert [name for name, _ in events] == ["progress", "progress", "result", "done"]
    result_html = events[2][1]
    assert "Copied 2 folders, 0 failed." in result_html
    assert (dest / "sample_src_02" / "hello.txt").read_text(encoding="utf-8") == "world"
    assert (dest / "sample_src_03" / "sub" / "item.txt").read_text(encoding="utf-8") == "nested"

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "folder-copy"
    assert runs[0].status == "succeeded"
    assert runs[0].succeeded == 2

    history_pane = client.get("/history", headers=HX).text
    assert "<code>folder-copy</code>" in history_pane
    assert "Succeeded" in history_pane

    # Existing target without overwrite returns 422 before starting a run.
    conflict_status, conflict_html = _start(
        client,
        source=str(src),
        dest_dir=str(dest),
        fmt="{name}_{n:02d}",
        start="2",
        count="1",
        overwrite=False,
    )
    assert conflict_status == 422
    assert "Target already exists" in conflict_html

    # Checking overwrite succeeds and replaces existing folders.
    overwrite_status, overwrite_html = _start(
        client,
        source=str(src),
        dest_dir=str(dest),
        fmt="{name}_{n:02d}",
        start="2",
        count="1",
        overwrite=True,
    )
    assert overwrite_status == 200
    overwrite_events = _events(client, overwrite_html)
    assert "Copied 1 folders, 0 failed." in overwrite_events[1][1]


def test_lock_conflict_streams_error_and_records_history(
    client: TestClient, tmp_path: Path, env_state: tuple[Path, HistoryStore]
) -> None:
    state, store = env_state
    src, dest = _seed_source(tmp_path)
    target = (dest / "sample_src_001").resolve()

    with target_lock(target, state_root=state):
        status, html = _start(client, source=str(src), dest_dir=str(dest), count="1")
        assert status == 200
        events = _events(client, html)

    assert [name for name, _ in events] == ["error", "done"]
    assert "Target is locked by another run" in events[0][1]
    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"


def test_stream_cancel_omitted_defaults_and_report_capping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env_state: tuple[Path, HistoryStore]
) -> None:
    state, _store = env_state
    src, dest = _seed_source(tmp_path)

    done = 0

    def cancel() -> bool:
        return done >= 1

    stream = stream_folder_copy(
        {"source": [str(src)], "dest_dir": [str(dest)], "count": ["2"]},
        state_root=state,
    )
    events: list[object] = []
    for value in stream(cancel):
        events.append(value)
        if isinstance(value, Progress):
            done += 1

    last = events[-1]
    assert isinstance(last, Report)
    result = cast("FolderCopyResult", last.context["result"])
    assert result.batch.cancelled is True

    monkeypatch.setattr(screens, "SHOWN_ITEMS", 1)
    capped = folder_copy_report(
        FolderCopyResult(
            source=src.resolve(),
            dest_dir=dest.resolve(),
            items=((dest / "a").resolve(), (dest / "b").resolve()),
            dry_run=False,
            batch=BatchResult(2, (ItemFailure(Path("bad"), "access is denied"),), cancelled=True),
        )
    )
    assert len(cast("tuple[Path, ...]", capped.context["shown_items"])) == 1
