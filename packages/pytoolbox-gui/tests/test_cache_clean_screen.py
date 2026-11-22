import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from pytoolbox.db import HistoryStore
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, ItemFailure, Progress
from pytoolbox.tools.system.cleanup import CACHE_TARGETS, CacheCleanResult, CacheItem
from pytoolbox_gui import screens
from pytoolbox_gui.screens import cache_clean_report, stream_cache_clean
from pytoolbox_gui.server import create_app
from pytoolbox_gui.sse import Report

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture
def env_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, HistoryStore]:
    local = tmp_path / "local"
    home = tmp_path / "home"
    state = tmp_path / "state"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    store = HistoryStore(state_root=state)
    return local, home, state, store


@pytest.fixture
def client(env_state: tuple[Path, Path, Path, HistoryStore]) -> Iterator[TestClient]:
    _local, _home, _state, store = env_state
    with TestClient(create_app(history=store), base_url="http://127.0.0.1:8000") as test:
        yield test


def _start(client: TestClient, targets: list[str], *, apply: bool = False) -> tuple[int, str]:
    data: dict[str, str | list[str]] = {"target": targets}
    if apply:
        data["apply"] = "true"
    response = client.post("/tools/cache-clean/runs", data=data, headers={**ORIGIN, **HX})
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


def _seed_caches(local: Path) -> tuple[Path, Path, Path]:
    old_temp = local / "Temp" / "old.tmp"
    old_temp.parent.mkdir(parents=True, exist_ok=True)
    old_temp.write_bytes(b"12345")
    old_ts = time.time() - 90000
    os.utime(old_temp, (old_ts, old_ts))

    fresh_temp = local / "Temp" / "fresh.tmp"
    fresh_temp.write_bytes(b"999")

    pip_file = local / "pip" / "Cache" / "pkg.whl"
    pip_file.parent.mkdir(parents=True, exist_ok=True)
    pip_file.write_bytes(b"abcdefgh")
    return old_temp, fresh_temp, pip_file


def test_page_defaults_targets_and_apply_unchecked(client: TestClient) -> None:
    pane = client.get("/tools/cache-clean", headers=HX).text

    assert 'hx-post="/tools/cache-clean/runs"' in pane
    assert 'hx-target="#cache-clean-run"' in pane
    assert '<button type="submit">Start</button>' in pane
    assert " checked" not in pane
    for target in CACHE_TARGETS:
        assert f'<input type="checkbox" name="target" value="{target}">' in pane
    assert '<input type="checkbox" name="apply" value="true">' in pane


@pytest.mark.parametrize(
    ("targets", "message"),
    [
        ([], "Specify at least one cache target"),
        (["bogus"], "Unknown cache target: bogus"),
    ],
)
def test_bad_input_is_422_before_the_run_starts(
    client: TestClient, targets: list[str], message: str
) -> None:
    status, html = _start(client, targets)

    assert status == 422
    assert message in html


def test_dry_run_streams_progress_and_plans_without_deleting_or_recording_history(
    client: TestClient, env_state: tuple[Path, Path, Path, HistoryStore]
) -> None:
    local, _home, _state, store = env_state
    old_temp, fresh_temp, pip_file = _seed_caches(local)

    status, html = _start(client, ["temp", "pip"], apply=False)

    assert status == 200
    events = _events(client, html)
    assert [name for name, _ in events] == ["progress", "progress", "result", "done"]
    result = events[2][1]
    assert "Dry-run: 2 files planned (13 B, 13 bytes)." in result
    assert str(old_temp) in result
    assert str(pip_file) in result
    assert str(fresh_temp) not in result
    assert old_temp.exists()
    assert fresh_temp.exists()
    assert pip_file.exists()
    assert not store.path.exists()


def test_apply_deletes_eligible_files_keeps_fresh_temp_and_records_history(
    client: TestClient, env_state: tuple[Path, Path, Path, HistoryStore]
) -> None:
    local, _home, _state, store = env_state
    old_temp, fresh_temp, pip_file = _seed_caches(local)

    status, html = _start(client, ["temp", "pip"], apply=True)

    assert status == 200
    events = _events(client, html)
    assert [name for name, _ in events] == ["progress", "progress", "result", "done"]
    result = events[2][1]
    assert "Applied: 2 files deleted (13 B, 13 bytes), 0 failed." in result
    assert not old_temp.exists()
    assert fresh_temp.exists()
    assert not pip_file.exists()
    assert (local / "pip" / "Cache").is_dir()

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "cache-clean"
    assert runs[0].arguments == {"apply": True, "targets": ["temp", "pip"]}
    assert runs[0].succeeded == 2
    assert runs[0].status == "succeeded"

    history_pane = client.get("/history", headers=HX).text
    assert "<code>cache-clean</code>" in history_pane
    assert "Succeeded" in history_pane


def test_lock_conflict_streams_error_event_and_records_history(
    client: TestClient, env_state: tuple[Path, Path, Path, HistoryStore]
) -> None:
    local, _home, state, store = env_state
    _old_temp, _fresh_temp, pip_file = _seed_caches(local)

    with target_lock(local / "pip" / "Cache", state_root=state):
        status, html = _start(client, ["pip"], apply=True)
        assert status == 200
        events = _events(client, html)

    assert [name for name, _ in events] == ["error", "done"]
    assert "Target is locked by another run" in events[0][1]
    assert pip_file.exists()
    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"


def test_report_caps_rows_and_cancel_stops_stream(
    monkeypatch: pytest.MonkeyPatch, env_state: tuple[Path, Path, Path, HistoryStore]
) -> None:
    local, _home, state, _store = env_state
    pip_dir = local / "pip" / "Cache"
    pip_dir.mkdir(parents=True)
    a = pip_dir / "a.whl"
    b = pip_dir / "b.whl"
    a.write_bytes(b"aa")
    b.write_bytes(b"bb")

    done = 0

    def cancel() -> bool:
        return done >= 1

    stream = stream_cache_clean({"target": ["pip"]}, state_root=state)
    events: list[object] = []
    for value in stream(cancel):
        events.append(value)
        if isinstance(value, Progress):
            done += 1

    last = events[-1]
    assert isinstance(last, Report)
    result = cast("CacheCleanResult", last.context["result"])
    assert result.batch.cancelled is True

    monkeypatch.setattr(screens, "SHOWN_ITEMS", 1)
    capped = cache_clean_report(
        CacheCleanResult(
            targets=("pip",),
            items=(CacheItem("pip", a, 2), CacheItem("pip", b, 2)),
            total_bytes=4,
            dry_run=False,
            batch=BatchResult(2, (ItemFailure(Path("bad"), "access is denied"),), cancelled=True),
        )
    )
    rows = cast("list[tuple[CacheItem, str]]", capped.context["rows"])
    assert len(rows) == 1
