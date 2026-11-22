import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pytoolbox.db import HISTORY_FILENAME, HistoryStore, RunRecord, history_path
from pytoolbox.errors import InputError, OperationError


def _t(minutes: int) -> datetime:
    return datetime(2026, 11, 22, 12, minutes, 0, tzinfo=UTC)


def test_history_path_uses_state_root(tmp_path: Path) -> None:
    assert history_path(state_root=tmp_path) == tmp_path / HISTORY_FILENAME


def test_empty_store_returns_empty_and_does_not_create_db(tmp_path: Path) -> None:
    store = HistoryStore(state_root=tmp_path)

    assert store.list_runs() == ()
    assert store.recent_tools() == ()
    assert store.list_tools() == ()
    assert not store.path.exists()


def test_record_persists_utc_iso_and_lists_newest_first_with_filters(tmp_path: Path) -> None:
    store = HistoryStore(state_root=tmp_path)
    jst = timezone(timedelta(hours=9))
    start_jst = datetime(2026, 11, 22, 21, 0, 0, tzinfo=jst)
    finish_jst = datetime(2026, 11, 22, 21, 0, 5, tzinfo=jst)

    first = store.record(
        "csv-gen",
        {"rows": 100, "out": tmp_path / "out.csv"},
        started_at=start_jst,
        finished_at=finish_jst,
        succeeded=1,
    )
    second = store.record(
        "file-copy",
        {"count": 3},
        started_at=_t(10),
        finished_at=_t(11),
        succeeded=2,
        failed=1,
    )
    third = store.record(
        "csv-gen",
        {"rows": 50},
        started_at=_t(20),
        finished_at=_t(21),
        cancelled=True,
    )

    assert first.started_at == datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
    assert first.finished_at == datetime(2026, 11, 22, 12, 0, 5, tzinfo=UTC)
    assert first.arguments == {"rows": 100, "out": str(tmp_path / "out.csv")}

    all_runs = store.list_runs()
    assert [row.id for row in all_runs] == [third.id, second.id, first.id]
    assert [row.id for row in store.list_runs(limit=2)] == [third.id, second.id]
    assert store.list_runs(limit=0) == ()
    assert [row.id for row in store.list_runs(tool="csv-gen")] == [third.id, first.id]
    assert store.list_runs(tool="unknown") == ()
    assert store.list_tools() == ("csv-gen", "file-copy")


def test_store_keeps_every_row_with_no_cap(tmp_path: Path) -> None:
    store = HistoryStore(state_root=tmp_path)
    for index in range(250):
        store.record(
            "file-copy",
            {"index": index},
            started_at=_t(0) + timedelta(seconds=index),
            finished_at=_t(0) + timedelta(seconds=index + 1),
            succeeded=1,
        )

    assert len(store.list_runs()) == 250
    assert len(store.list_runs(limit=200)) == 200


def test_recent_tools_returns_distinct_tools_by_latest_run(tmp_path: Path) -> None:
    store = HistoryStore(state_root=tmp_path)
    for index, tool in enumerate(("csv-gen", "file-copy", "pdf-split", "csv-gen", "cache-clean")):
        store.record(
            tool,
            {"step": index},
            started_at=_t(index),
            finished_at=_t(index) + timedelta(seconds=1),
            succeeded=1,
        )

    assert store.recent_tools() == ("cache-clean", "csv-gen", "pdf-split", "file-copy")
    assert store.recent_tools(limit=2) == ("cache-clean", "csv-gen")
    assert store.recent_tools(limit=0) == ()


def test_run_record_status_covers_all_outcomes() -> None:
    def make(
        *, succeeded: int, failed: int, cancelled: bool, error: str | None = None
    ) -> RunRecord:
        return RunRecord(
            id=1,
            tool="cache-clean",
            arguments={},
            started_at=_t(0),
            finished_at=_t(1),
            succeeded=succeeded,
            failed=failed,
            cancelled=cancelled,
            error=error,
        )

    assert make(succeeded=3, failed=0, cancelled=False).status == "succeeded"
    assert make(succeeded=2, failed=1, cancelled=False).status == "partial"
    assert make(succeeded=0, failed=2, cancelled=False).status == "failed"
    assert (
        make(succeeded=1, failed=0, cancelled=False, error="Target is locked.").status == "failed"
    )
    assert make(succeeded=1, failed=1, cancelled=True, error="boom").status == "cancelled"


def test_record_and_queries_validate_inputs(tmp_path: Path) -> None:
    store = HistoryStore(state_root=tmp_path)
    naive = datetime(2026, 11, 22, 12, 0, 0)  # noqa: DTZ001 - intentionally naive for validation test

    with pytest.raises(InputError, match="Tool name"):
        store.record("   ", {}, started_at=_t(0), finished_at=_t(1))
    with pytest.raises(InputError, match="non-negative"):
        store.record("csv-gen", {}, started_at=_t(0), finished_at=_t(1), succeeded=-1)
    with pytest.raises(InputError, match="non-negative"):
        store.record("csv-gen", {}, started_at=_t(0), finished_at=_t(1), failed=-1)
    with pytest.raises(InputError, match="timezone-aware"):
        store.record("csv-gen", {}, started_at=naive, finished_at=_t(1))
    with pytest.raises(InputError, match="timezone-aware"):
        store.record("csv-gen", {}, started_at=_t(0), finished_at=naive)
    with pytest.raises(InputError, match="Limit"):
        store.list_runs(limit=-1)
    with pytest.raises(InputError, match="Limit"):
        store.recent_tools(limit=-1)


def test_corrupt_or_unwritable_database_raises_operation_error(tmp_path: Path) -> None:
    file_parent = tmp_path / "file_parent"
    file_parent.write_text("x", encoding="utf-8")
    blocked_store = HistoryStore(file_parent / "history.sqlite3")
    with pytest.raises(OperationError, match="Cannot write run history"):
        blocked_store.record("csv-gen", {}, started_at=_t(0), finished_at=_t(1))

    corrupt_path = tmp_path / "corrupt.sqlite3"
    corrupt_path.write_text("not sqlite", encoding="utf-8")
    corrupt_store = HistoryStore(corrupt_path)
    with pytest.raises(OperationError, match="Cannot read run history"):
        corrupt_store.list_runs()
    with pytest.raises(OperationError, match="Cannot read run history"):
        corrupt_store.recent_tools()
    with pytest.raises(OperationError, match="Cannot read run history"):
        corrupt_store.list_tools()

    bad_json_db = tmp_path / "bad_json.sqlite3"
    store = HistoryStore(bad_json_db)
    store.record("csv-gen", {}, started_at=_t(0), finished_at=_t(1))
    with sqlite3.connect(bad_json_db) as conn:
        conn.execute("UPDATE runs SET arguments = '[]'")
        conn.commit()
    with pytest.raises(OperationError, match="Invalid arguments JSON"):
        store.list_runs()
