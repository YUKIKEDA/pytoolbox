import csv
import errno
import io
from pathlib import Path

import pytest

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError
from pytoolbox.locks import target_lock
from pytoolbox.tools.dev import csv_gen
from pytoolbox.tools.dev.csv_gen import CsvColumn, generate_csv, parse_columns


def test_parse_columns_accepts_all_types_and_ignores_blank_lines() -> None:
    cols = parse_columns(["  name : TEXT ", "", "age:number", "ok:boolean", "when:date", "   "])

    assert cols == (
        CsvColumn("name", "text"),
        CsvColumn("age", "number"),
        CsvColumn("ok", "boolean"),
        CsvColumn("when", "date"),
    )


@pytest.mark.parametrize(
    ("specs", "pattern"),
    [
        ([], "at least one column"),
        (["  ", ""], "at least one column"),
        (["name"], "must use NAME:TYPE"),
        ([":text"], "must not be empty"),
        (["name:uuid"], "Unknown column type"),
    ],
)
def test_parse_columns_rejects_invalid_specs(specs: list[str], pattern: str) -> None:
    with pytest.raises(InputError, match=pattern):
        parse_columns(specs)


def test_generate_csv_writes_utf8_bom_crlf_and_records_history(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "nested" / "data.csv"
    cols = parse_columns(["label:text", "count:number", "flag:boolean", "day:date"])

    result = generate_csv(out, 5, cols, seed=42, state_root=state)

    assert result.output == out.resolve()
    assert result.rows == 5
    assert result.seed == 42
    assert not result.dry_run

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    decoded = raw.decode("utf-8-sig")
    parsed_rows = list(csv.reader(io.StringIO(decoded)))
    assert len(parsed_rows) == 6
    assert parsed_rows[0] == ["label", "count", "flag", "day"]
    for row in parsed_rows[1:]:
        assert "_" in row[0]
        assert int(row[1]) >= 0
        assert row[2] in {"true", "false"}
        assert len(row[3]) == 10

    # Same seed reproduces the exact bytes.
    out2 = tmp_path / "nested" / "data2.csv"
    generate_csv(out2, 5, cols, seed=42, state_root=state)
    assert out2.read_bytes() == raw

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 2
    assert runs[0].tool == "csv-gen"
    assert runs[0].succeeded == 1
    assert runs[0].failed == 0
    assert runs[0].arguments["rows"] == 5


def test_generate_csv_dry_run_writes_nothing_and_leaves_no_history(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "preview.csv"
    cols = (CsvColumn("id", "number"),)

    result = generate_csv(out, 3, cols, dry_run=True, state_root=state)

    assert result.dry_run
    assert result.output == out.resolve()
    assert not out.exists()
    assert HistoryStore(state_root=state).list_runs() == ()


def test_generate_csv_respects_overwrite_and_rejects_bad_inputs(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "existing.csv"
    out.write_text("old", encoding="utf-8")
    cols = (CsvColumn("name", "text"),)

    with pytest.raises(InputError, match="already exists"):
        generate_csv(out, 2, cols, state_root=state)
    assert out.read_text(encoding="utf-8") == "old"

    generate_csv(out, 2, cols, seed=1, overwrite=True, state_root=state)
    assert out.read_bytes().startswith(b"\xef\xbb\xbfname\r\n")

    with pytest.raises(InputError, match="at least 1"):
        generate_csv(tmp_path / "a.csv", 0, cols, state_root=state)
    with pytest.raises(InputError, match="at least one column"):
        generate_csv(tmp_path / "a.csv", 1, (), state_root=state)
    with pytest.raises(InputError, match="must not be empty"):
        generate_csv(Path("   "), 1, cols, state_root=state)
    with pytest.raises(InputError, match="is a directory"):
        generate_csv(tmp_path, 1, cols, state_root=state)


def test_generate_csv_refuses_symlinks(tmp_path: Path) -> None:
    state = tmp_path / "state"
    real = tmp_path / "real.csv"
    link = tmp_path / "link.csv"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks require elevated privileges on this Windows account")

    with pytest.raises(InputError, match="Links and junctions"):
        generate_csv(link, 2, (CsvColumn("name", "text"),), state_root=state)


def test_generate_csv_records_failure_on_lock_conflict_and_write_error(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "locked.csv"
    cols = (CsvColumn("name", "text"),)

    with (
        target_lock(out, state_root=state),
        pytest.raises(OperationError, match="Target is locked by another run"),
    ):
        generate_csv(out, 2, cols, state_root=state)

    assert not out.exists()
    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "Target is locked by another run" in runs[0].error

    file_parent = tmp_path / "file_parent"
    file_parent.write_text("x", encoding="utf-8")
    with pytest.raises(OperationError, match="Cannot write"):
        generate_csv(file_parent / "child.csv", 2, cols, state_root=state)

    assert csv_gen._os_error_reason(OSError(errno.EACCES, "localized")) == "access is denied"
    assert csv_gen._os_error_reason(OSError(errno.ENOLCK, "no locks")) == "no locks"
