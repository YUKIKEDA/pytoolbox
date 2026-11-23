from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from pytoolbox.db import HistoryStore
from pytoolbox.locks import target_lock
from pytoolbox.results import BatchResult, ItemFailure
from pytoolbox.tools.system.pdf_split import PdfSplitResult
from pytoolbox_gui.screens import pdf_split_report
from pytoolbox_gui.server import create_app

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


def _make_pdf(path: Path, pages: int = 3) -> Path:
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=200 + index * 10, height=300)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _start(
    client: TestClient,
    source: str,
    output_dir: str,
    *,
    fmt: str = "page_{n}.pdf",
    dry_run: bool = False,
    overwrite: bool = False,
) -> tuple[int, str]:
    data: dict[str, str] = {
        "source": source,
        "output_dir": output_dir,
        "format": fmt,
    }
    if dry_run:
        data["dry_run"] = "on"
    if overwrite:
        data["overwrite"] = "on"
    response = client.post("/tools/pdf-split/runs", data=data, headers={**ORIGIN, **HX})
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


def test_pdf_split_screen_dry_run_split_overwrite_and_history(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, store = client
    assert test.get("/static/icons/ic_fluent_document_split_hint_20_regular.svg").status_code == 200

    page = test.get("/tools/pdf-split", headers=HX).text
    assert 'hx-post="/tools/pdf-split/runs"' in page
    assert "page_{n}.pdf" in page

    src = _make_pdf(tmp_path / "sample.pdf", pages=3)
    out_dir = tmp_path / "pages"

    status, dry_html = _start(test, f'"{src}"', f'"{out_dir}"', dry_run=True)
    assert status == 200
    dry_events = _events(test, dry_html)
    assert [name for name, _ in dry_events if name == "progress"]
    dry_result = dict(dry_events)["result"]
    assert "3 pages planned" in dry_result
    assert not out_dir.exists()
    assert store.list_runs() == ()

    status, run_html = _start(test, str(src), str(out_dir))
    assert status == 200
    run_events = _events(test, run_html)
    run_result = dict(run_events)["result"]
    assert "3 pages written" in run_result
    for page_num in range(1, 4):
        page_file = out_dir / f"page_{page_num}.pdf"
        assert page_file.is_file()
        assert len(PdfReader(page_file).pages) == 1

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "pdf-split"
    assert runs[0].succeeded == 3
    history_pane = test.get("/history", headers=HX).text
    assert "pdf-split" in history_pane

    status_conflict, conflict_html = _start(test, str(src), str(out_dir))
    assert status_conflict == 422
    assert "already exists" in conflict_html

    status_over, over_html = _start(test, str(src), str(out_dir), overwrite=True)
    assert status_over == 200
    _events(test, over_html)
    assert len(store.list_runs()) == 2


def test_pdf_split_screen_validation_and_lock_error(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, store = client
    state = store.path.parent
    src = _make_pdf(tmp_path / "sample.pdf", pages=2)

    for data, expected in [
        ({"source": "", "output_dir": str(tmp_path / "out")}, "Source PDF: enter a path"),
        (
            {"source": str(src), "output_dir": ""},
            "Output folder: enter a path",
        ),
        (
            {"source": "relative.pdf", "output_dir": str(tmp_path / "out")},
            "use an absolute path",
        ),
        (
            {"source": str(src), "output_dir": "relative_out"},
            "use an absolute path",
        ),
        (
            {"source": str(src), "output_dir": str(tmp_path / "out"), "format": "{bad}.pdf"},
            "Invalid filename format",
        ),
    ]:
        payload = {"format": "page_{n}.pdf", **data}
        response = test.post("/tools/pdf-split/runs", data=payload, headers={**ORIGIN, **HX})
        assert response.status_code == 422
        assert expected in response.text

    locked_dir = tmp_path / "locked_out"
    with target_lock(locked_dir.resolve() / "page_1.pdf", state_root=state):
        status, run_html = _start(test, str(src), str(locked_dir))
        assert status == 200
        events = dict(_events(test, run_html))
        assert "error" in events
        assert "Target is locked" in events["error"]


def test_pdf_split_report_truncates_over_1000_and_renders_failures(
    tmp_path: Path, client: tuple[TestClient, HistoryStore]
) -> None:
    test, _store = client
    items = tuple(tmp_path / f"page_{i}.pdf" for i in range(1, 1005))
    result = PdfSplitResult(
        source=tmp_path / "big.pdf",
        output_dir=tmp_path,
        items=items,
        dry_run=False,
        batch=BatchResult(1004, (ItemFailure(tmp_path / "bad.pdf", "access is denied"),), True),
    )
    report = pdf_split_report(result)
    rows = cast("list[tuple[int, Path]]", report.context["rows"])
    assert len(rows) == 1000
    assert test.get("/tools/pdf-split").status_code == 200
