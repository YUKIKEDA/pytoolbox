import errno
import ipaddress
import json
import uuid
from pathlib import Path

import pytest

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError
from pytoolbox.locks import target_lock
from pytoolbox.tools.dev import json_gen
from pytoolbox.tools.dev.json_gen import generate_json, load_schema_input


def test_generate_json_supports_all_13_types_and_is_deterministic_with_seed(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    out1 = tmp_path / "out1.json"
    out2 = tmp_path / "out2.json"
    schema: dict[str, object] = {
        "s": "string",
        "n": {"type": "number", "min": 10, "max": 20},
        "f": {"type": "number", "min": 1.0, "max": 2.0},
        "b": "boolean",
        "d": {"type": "date", "start": "2024-01-01", "end": "2024-01-31"},
        "e": "email",
        "u": "uuid",
        "url": "url",
        "ip": "ip",
        "p": "path",
        "arr": {"type": "array", "items": "number", "length": 3},
        "obj": {"type": "object", "fields": {"k": "string"}},
        "obj_default": "object",
        "dr": {"type": "date_range", "start": "2024-01-01", "end": "2024-02-01"},
        "cl": {"type": "custom_list", "values": ["red", "blue"]},
    }

    res1 = generate_json(out1, 4, schema, seed=99, state_root=state)
    generate_json(out2, 4, schema, seed=99, state_root=state)

    assert not res1.dry_run
    assert res1.rows == 4
    raw = out1.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert out2.read_bytes() == raw

    records = json.loads(raw.decode("utf-8"))
    assert len(records) == 4
    row = records[0]
    assert isinstance(row["s"], str)
    assert 10 <= row["n"] <= 20
    assert 1.0 <= row["f"] <= 2.0
    assert isinstance(row["b"], bool)
    assert row["d"].startswith("2024-01-")
    assert "@example.com" in row["e"]
    assert uuid.UUID(row["u"]).version == 4
    assert row["url"].startswith("https://example.com/")
    assert isinstance(ipaddress.ip_address(row["ip"]), ipaddress.IPv4Address)
    assert row["p"].startswith("/var/data/")
    assert len(row["arr"]) == 3
    assert isinstance(row["obj"]["k"], str)
    assert set(row["obj_default"].keys()) == {"key", "value"}
    assert row["dr"]["start"] <= row["dr"]["end"]
    assert row["cl"] in {"red", "blue"}

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 2
    assert runs[0].tool == "json-gen"
    assert runs[0].succeeded == 1


def test_load_schema_input_handles_inline_file_and_cols(tmp_path: Path) -> None:
    assert load_schema_input('{"name": "string"}') == {"name": "string"}
    assert load_schema_input(columns=["id:uuid", "ok:boolean"]) == {
        "id": "uuid",
        "ok": "boolean",
    }

    schema_file = tmp_path / "schema.json"
    schema_file.write_text(
        '{"role": {"type": "custom_list", "values": ["a"]}}', encoding="utf-8-sig"
    )
    assert load_schema_input(str(schema_file)) == {"role": {"type": "custom_list", "values": ["a"]}}


@pytest.mark.parametrize(
    ("schema_text", "cols", "pattern"),
    [
        (None, None, "Specify a schema"),
        ("{}", ["a:string"], "not both"),
        ("bad", None, "was not found"),
        ("{bad", None, "Invalid JSON schema"),
        ("[1, 2]", None, "must be a JSON object"),
        (None, ["broken"], "must use NAME:TYPE"),
    ],
)
def test_load_schema_input_rejects_bad_inputs(
    schema_text: str | None, cols: list[str] | None, pattern: str
) -> None:
    with pytest.raises(InputError, match=pattern):
        load_schema_input(schema_text, cols)


def test_load_schema_input_rejects_non_utf8_and_unreadable(tmp_path: Path) -> None:
    bad_utf8 = tmp_path / "bad.json"
    bad_utf8.write_bytes(b"\xff\xfe")
    with pytest.raises(InputError, match="not valid UTF-8"):
        load_schema_input(str(bad_utf8))

    link = tmp_path / "link.json"
    try:
        link.symlink_to(bad_utf8)
    except OSError:
        pytest.skip("symlinks require elevated privileges")
    with pytest.raises(InputError, match="Links and junctions"):
        load_schema_input(str(link))


@pytest.mark.parametrize(
    ("schema", "pattern"),
    [
        ({}, "at least one property"),
        ({"  ": "string"}, "must not be empty"),
        ({"a": 123}, "type string or object"),
        ({"a": {}}, "requires a string 'type'"),
        ({"a": "nope"}, "Unknown type"),
        ({"a": {"type": "number", "min": "x"}}, "must be a number"),
        ({"a": {"type": "number", "max": True}}, "must be a number"),
        ({"a": {"type": "number", "min": 5, "max": 1}}, "min > max"),
        ({"a": {"type": "date", "start": 123}}, "ISO YYYY-MM-DD"),
        ({"a": {"type": "date", "start": "2026-99-99"}}, "Invalid"),
        ({"a": {"type": "date_range", "start": "2026-05-01", "end": "2025-01-01"}}, "start date"),
        ({"a": "custom_list"}, "non-empty 'values' list"),
        ({"a": {"type": "array", "length": -1}}, "non-negative"),
        ({"a": {"type": "array", "length": True}}, "must be an integer"),
        ({"a": {"type": "object", "fields": {}}}, "non-empty object"),
    ],
)
def test_generate_json_validates_schema_errors(
    tmp_path: Path, schema: dict[str, object], pattern: str
) -> None:
    with pytest.raises(InputError, match=pattern):
        generate_json(tmp_path / "out.json", 1, schema, state_root=tmp_path / "state")


def test_generate_json_dry_run_overwrite_lock_and_errors(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "data.json"
    schema: dict[str, object] = {"name": "string"}

    dry = generate_json(out, 2, schema, dry_run=True, state_root=state)
    assert dry.dry_run
    assert not out.exists()
    assert HistoryStore(state_root=state).list_runs() == ()

    with pytest.raises(InputError, match="at least 1"):
        generate_json(out, 0, schema, state_root=state)
    with pytest.raises(InputError, match="must not be empty"):
        generate_json(Path(" "), 1, schema, state_root=state)
    with pytest.raises(InputError, match="is a directory"):
        generate_json(tmp_path, 1, schema, state_root=state)

    generate_json(out, 1, schema, state_root=state)
    with pytest.raises(InputError, match="already exists"):
        generate_json(out, 1, schema, state_root=state)

    generate_json(out, 2, schema, overwrite=True, state_root=state)
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 2

    locked = tmp_path / "locked.json"
    with (
        target_lock(locked, state_root=state),
        pytest.raises(OperationError, match="Target is locked by another run"),
    ):
        generate_json(locked, 1, schema, state_root=state)
    assert HistoryStore(state_root=state).list_runs()[0].status == "failed"

    file_parent = tmp_path / "file_parent"
    file_parent.write_text("x", encoding="utf-8")
    with pytest.raises(OperationError, match="Cannot write"):
        generate_json(file_parent / "child.json", 1, schema, state_root=state)

    assert json_gen._os_error_reason(OSError(errno.EACCES, "localized")) == "access is denied"
    assert json_gen._os_error_reason(OSError(errno.ENOLCK, "no locks")) == "no locks"
