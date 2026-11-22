from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pytoolbox.db import HistoryStore
from pytoolbox.registry import System, Tool
from pytoolbox_gui.screens import Fragment, Screen
from pytoolbox_gui.server import create_app


def _make_tools() -> tuple[tuple[Tool, ...], tuple[Screen, ...]]:
    tools = tuple(
        Tool(f"tool-{index}", System.OPERATIONS, "Files", f"Summary {index}.")
        for index in range(1, 8)
    )
    screens = tuple(
        Screen(tool, "pane.html", "home", run=lambda _form: Fragment("pane.html", {}))
        for tool in tools
    )
    return tools, screens


def _t(minutes: int) -> datetime:
    return datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC) + timedelta(minutes=minutes)


@pytest.fixture
def templates(tmp_path: Path) -> Path:
    (tmp_path / "pane.html").write_text("<p>tool</p>", encoding="utf-8")
    return tmp_path


def _client(tmp_path: Path, templates: Path) -> tuple[TestClient, HistoryStore]:
    tools, all_screens = _make_tools()
    store = HistoryStore(state_root=tmp_path)
    app = create_app(tools, all_screens, template_dirs=(templates,), history=store)
    return TestClient(app, base_url="http://127.0.0.1:8000"), store


def test_history_entry_and_empty_states(tmp_path: Path, templates: Path) -> None:
    client, _store = _client(tmp_path, templates)
    with client:
        assert client.get("/static/icons/ic_fluent_history_20_regular.svg").status_code == 200

        home = client.get("/").text
        assert 'href="/history"' in home
        assert "Logs &amp; History" in home
        assert "Recently used" not in home

        full = client.get("/history").text
        pane = client.get("/history", headers={"HX-Request": "true"}).text
        assert "<nav" in full
        assert "<nav" not in pane
        assert "No run history yet." in pane
        assert "Clear" not in pane


def test_history_page_lists_latest_200_filters_and_expands_details(
    tmp_path: Path, templates: Path
) -> None:
    client, store = _client(tmp_path, templates)
    store.record(
        "tool-1",
        {"target": "<escaped>"},
        started_at=_t(1),
        finished_at=_t(2),
        succeeded=4,
    )
    store.record(
        "tool-2",
        {"count": 2},
        started_at=_t(3),
        finished_at=_t(4),
        succeeded=1,
        failed=1,
    )
    store.record(
        "tool-3",
        {"mode": "apply"},
        started_at=_t(5),
        finished_at=_t(6),
        succeeded=0,
        failed=0,
        error="Target is locked: <file>\nsecond detail line",
    )
    store.record(
        "tool-1",
        {"rows": 10},
        started_at=_t(7),
        finished_at=_t(8),
        succeeded=3,
        cancelled=True,
    )

    with client:
        pane = client.get("/history", headers={"HX-Request": "true"}).text
        assert "Succeeded" in pane
        assert "Partial" in pane
        assert "Failed" in pane
        assert "Cancelled" in pane
        assert "&lt;escaped&gt;" in pane
        assert "&lt;file&gt;\nsecond detail line" in pane
        assert "<details" in pane
        assert pane.index("Cancelled") < pane.index("Failed") < pane.index("Partial")
        assert pane.index("Partial") < pane.index("Succeeded")

        filtered = client.get(
            "/history", params={"tool": "tool-1"}, headers={"HX-Request": "true"}
        ).text
        assert filtered.count('class="history-row-') == 2
        assert "tool-2" not in filtered.split("<tbody>", maxsplit=1)[1]

        missing = client.get(
            "/history", params={"tool": "unknown-tool"}, headers={"HX-Request": "true"}
        ).text
        assert "No history entries for “unknown-tool”." in missing


def test_history_page_caps_display_at_200_rows(tmp_path: Path, templates: Path) -> None:
    client, store = _client(tmp_path, templates)
    for index in range(205):
        store.record(
            "tool-1",
            {"seq": index},
            started_at=_t(index),
            finished_at=_t(index) + timedelta(seconds=1),
            succeeded=1,
        )

    with client:
        pane = client.get("/history", headers={"HX-Request": "true"}).text
        assert pane.count('class="history-row-') == 200
        assert "&#34;seq&#34;: 204" in pane
        assert "&#34;seq&#34;: 0" not in pane


def test_home_recently_used_shows_up_to_6_distinct_registered_tools(
    tmp_path: Path, templates: Path
) -> None:
    client, store = _client(tmp_path, templates)
    sequence = (
        "tool-1",
        "tool-2",
        "tool-3",
        "tool-4",
        "tool-5",
        "tool-6",
        "tool-7",
        "tool-2",
        "removed-tool",
    )
    for index, name in enumerate(sequence):
        store.record(
            name,
            {"step": index},
            started_at=_t(index),
            finished_at=_t(index) + timedelta(seconds=1),
            succeeded=1,
        )

    with client:
        pane = client.get("/", headers={"HX-Request": "true"}).text
        recent_block = pane.split("<h2>Recently used</h2>", maxsplit=1)[1].split(
            "</section>", maxsplit=1
        )[0]
        assert recent_block.count('class="card"') == 6
        assert 'href="/tools/tool-2"' in recent_block
        assert 'href="/tools/tool-7"' in recent_block
        assert 'href="/tools/tool-1"' not in recent_block
        assert "removed-tool" not in recent_block
        assert recent_block.index("tool-2") < recent_block.index("tool-7")

        search_pane = client.get(
            "/search", params={"q": "Summary 1"}, headers={"HX-Request": "true"}
        ).text
        assert "Recently used" not in search_pane
