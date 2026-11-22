"""Synthetic JSON array file generation."""

import errno
import json
import random
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError, PytoolboxError
from pytoolbox.locks import target_lock

FIELD_TYPES: tuple[str, ...] = (
    "string",
    "number",
    "boolean",
    "date",
    "email",
    "uuid",
    "url",
    "ip",
    "path",
    "array",
    "object",
    "date_range",
    "custom_list",
)

DEFAULT_SCHEMA_TEXT = json.dumps(
    {
        "id": "uuid",
        "name": "string",
        "age": {"type": "number", "min": 18, "max": 80},
        "active": "boolean",
        "email": "email",
        "role": {"type": "custom_list", "values": ["admin", "member", "guest"]},
        "tags": {"type": "array", "items": "string", "length": 2},
        "period": {"type": "date_range", "start": "2024-01-01", "end": "2026-12-31"},
    },
    indent=2,
)

_WORDS: tuple[str, ...] = (
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
)
_DEFAULT_START = date(2020, 1, 1)
_DEFAULT_END = date(2026, 12, 31)

_OS_ERROR_REASONS: dict[int, str] = {
    errno.EACCES: "access is denied",
    errno.ENOENT: "path was not found",
    errno.ENOTDIR: "a parent path is not a directory",
    errno.EISDIR: "the path is a directory",
    errno.EROFS: "the file system is read-only",
}


def _os_error_reason(error: OSError) -> str:
    if error.errno is not None and error.errno in _OS_ERROR_REASONS:
        return _OS_ERROR_REASONS[error.errno]
    return error.strerror or str(error)


@dataclass(frozen=True)
class FieldSpec:
    """Validated generator specification for one JSON property."""

    kind: str
    options: Mapping[str, object]


@dataclass(frozen=True)
class JsonGenResult:
    """The result of a `json-gen` run.

    Attributes:
        output: Resolved destination JSON file path.
        rows: Number of objects generated (or planned on dry-run).
        fields: Top-level property names generated.
        seed: Random seed used, or `None` when non-deterministic.
        dry_run: Whether writing was skipped.
    """

    output: Path
    rows: int
    fields: tuple[str, ...]
    seed: int | None
    dry_run: bool


def _check_no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            msg = f"Links and junctions are not followed: {part}."
            raise InputError(msg)


def _parse_iso_date(raw: object, label: str) -> date:
    if not isinstance(raw, str):
        msg = f"{label} must be an ISO YYYY-MM-DD string."
        raise InputError(msg)
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        msg = f"Invalid {label} {raw!r}: use YYYY-MM-DD."
        raise InputError(msg) from error


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{label} must be an integer."
        raise InputError(msg)
    return value


def _validate_field(raw: object, name: str) -> FieldSpec:
    if isinstance(raw, str):
        kind = raw.strip().lower()
        options: dict[str, object] = {}
    elif isinstance(raw, Mapping):
        raw_map = cast("Mapping[object, object]", raw)
        raw_kind = raw_map.get("type")
        if not isinstance(raw_kind, str):
            msg = f"Field {name!r} requires a string 'type'."
            raise InputError(msg)
        kind = raw_kind.strip().lower()
        options = {str(k): v for k, v in raw_map.items()}
    else:
        msg = f"Field {name!r} must be a type string or object specification."
        raise InputError(msg)

    if kind not in FIELD_TYPES:
        msg = f"Unknown type {kind!r} for field {name!r}. Choose from: {', '.join(FIELD_TYPES)}."
        raise InputError(msg)

    if kind == "number":
        low = options.get("min", 0)
        high = options.get("max", 10_000)
        if isinstance(low, bool) or not isinstance(low, int | float):
            msg = f"Field {name!r} 'min' must be a number."
            raise InputError(msg)
        if isinstance(high, bool) or not isinstance(high, int | float):
            msg = f"Field {name!r} 'max' must be a number."
            raise InputError(msg)
        if low > high:
            msg = f"Field {name!r} has min > max."
            raise InputError(msg)
    elif kind in {"date", "date_range"}:
        start = (
            _parse_iso_date(options["start"], f"{name}.start")
            if "start" in options
            else _DEFAULT_START
        )
        end = _parse_iso_date(options["end"], f"{name}.end") if "end" in options else _DEFAULT_END
        if start > end:
            msg = f"Field {name!r} has start date after end date."
            raise InputError(msg)
    elif kind == "custom_list":
        values = options.get("values")
        if not isinstance(values, list) or not values:
            msg = f"Field {name!r} ('custom_list') requires a non-empty 'values' list."
            raise InputError(msg)
    elif kind == "array":
        length = options.get("length", 2)
        if _as_int(length, f"{name}.length") < 0:
            msg = f"Field {name!r} 'length' must be non-negative."
            raise InputError(msg)
        item_spec = options.get("items", "string")
        _validate_field(item_spec, f"{name}[]")
    elif kind == "object":
        fields_obj = options.get("fields", options.get("properties"))
        if fields_obj is not None:
            if not isinstance(fields_obj, Mapping) or not fields_obj:
                msg = f"Field {name!r} ('object') 'fields' must be a non-empty object."
                raise InputError(msg)
            parse_schema(cast("Mapping[str, object]", fields_obj))

    return FieldSpec(kind, options)


def parse_schema(raw: Mapping[str, object]) -> dict[str, FieldSpec]:
    """Validate and normalize a JSON object schema mapping.

    Args:
        raw: Mapping of property name to type name or option mapping.

    Returns:
        Normalized mapping of property names to `FieldSpec`.

    Raises:
        InputError: Schema is empty or any field specification is invalid.
    """
    if not raw:
        msg = "Schema must define at least one property."
        raise InputError(msg)
    parsed: dict[str, FieldSpec] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            msg = "Property names must not be empty."
            raise InputError(msg)
        parsed[name] = _validate_field(value, name)
    return parsed


def load_schema_input(
    schema_text_or_path: str | None = None,
    columns: Sequence[str] | None = None,
) -> dict[str, object]:
    """Load a raw schema mapping from `--schema` (path or inline JSON) or `--col` specs.

    Args:
        schema_text_or_path: Inline JSON object string or path to a JSON schema file.
        columns: Sequence of `NAME:TYPE` strings.

    Returns:
        Raw schema dictionary ready for `generate_json`.

    Raises:
        InputError: Missing input, conflicting inputs, invalid JSON, or missing schema file.
    """
    if schema_text_or_path is not None and columns:
        msg = "Pass either schema or --col, not both."
        raise InputError(msg)

    if columns:
        schema: dict[str, object] = {}
        for entry in columns:
            name, sep, kind = entry.strip().partition(":")
            if not sep or not name.strip():
                msg = f"Column {entry!r} must use NAME:TYPE."
                raise InputError(msg)
            schema[name.strip()] = kind.strip().lower()
        return schema

    if schema_text_or_path is None or not schema_text_or_path.strip():
        msg = "Specify a schema or at least one --col."
        raise InputError(msg)

    text = schema_text_or_path.strip()
    if text.startswith(("{", "[")):
        raw_text = text
    else:
        path = Path(text)
        _check_no_links(path)
        if not path.is_file():
            msg = f"Schema file was not found: {path}."
            raise InputError(msg)
        try:
            raw_text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            msg = f"Schema file {path} is not valid UTF-8."
            raise InputError(msg) from error
        except OSError as error:
            msg = f"Cannot read schema file {path}: {_os_error_reason(error)}."
            raise OperationError(msg) from error

    try:
        loaded: object = json.loads(raw_text)
    except json.JSONDecodeError as error:
        msg = f"Invalid JSON schema: {error.msg} (line {error.lineno}, column {error.colno})."
        raise InputError(msg) from error

    if not isinstance(loaded, dict):
        msg = "JSON schema must be a JSON object."
        raise InputError(msg)
    typed_loaded = cast("dict[object, object]", loaded)
    return {str(key): value for key, value in typed_loaded.items()}


def _random_date(start: date, end: date, rng: random.Random) -> date:
    days = (end - start).days
    return start + timedelta(days=rng.randint(0, days))


def _generate_value(spec: FieldSpec, rng: random.Random) -> object:
    kind = spec.kind
    opts = spec.options
    if kind == "string":
        return f"{rng.choice(_WORDS)}_{rng.randint(100, 999)}"
    if kind == "number":
        low = opts.get("min", 0)
        high = opts.get("max", 10_000)
        if isinstance(low, float) or isinstance(high, float):
            return round(rng.uniform(float(low), float(high)), 2)  # type: ignore[arg-type] - validated as int | float in _validate_field
        return rng.randint(int(low), int(high))  # type: ignore[arg-type] - validated as int in _validate_field
    if kind == "boolean":
        return rng.choice((True, False))
    if kind == "date":
        start = _parse_iso_date(opts["start"], "start") if "start" in opts else _DEFAULT_START
        end = _parse_iso_date(opts["end"], "end") if "end" in opts else _DEFAULT_END
        return _random_date(start, end, rng).isoformat()
    if kind == "email":
        return f"{rng.choice(_WORDS)}{rng.randint(1, 999)}@example.com"
    if kind == "uuid":
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if kind == "url":
        return f"https://example.com/{rng.choice(_WORDS)}/{rng.randint(1, 999)}"
    if kind == "ip":
        return (
            f"{rng.randint(10, 223)}.{rng.randint(0, 255)}."
            f"{rng.randint(0, 255)}.{rng.randint(1, 254)}"
        )
    if kind == "path":
        return f"/var/data/{rng.choice(_WORDS)}/{rng.randint(1, 999)}.txt"
    if kind == "custom_list":
        values = cast("list[object]", opts["values"])
        return rng.choice(values)
    if kind == "date_range":
        start = _parse_iso_date(opts["start"], "start") if "start" in opts else _DEFAULT_START
        end = _parse_iso_date(opts["end"], "end") if "end" in opts else _DEFAULT_END
        d1 = _random_date(start, end, rng)
        d2 = _random_date(d1, end, rng)
        return {"start": d1.isoformat(), "end": d2.isoformat()}
    if kind == "array":
        length = _as_int(opts.get("length", 2), "length")
        item_spec = _validate_field(opts.get("items", "string"), "items")
        return [_generate_value(item_spec, rng) for _ in range(length)]

    fields_raw = opts.get("fields", opts.get("properties"))
    if fields_raw is None:
        nested = {
            "key": FieldSpec("string", {}),
            "value": FieldSpec("number", {}),
        }
    else:
        nested = parse_schema(cast("Mapping[str, object]", fields_raw))
    return {key: _generate_value(sub_spec, rng) for key, sub_spec in nested.items()}


def generate_json(
    output: Path,
    rows: int,
    schema: Mapping[str, object],
    *,
    seed: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    state_root: Path | None = None,
) -> JsonGenResult:
    """Write a synthetic UTF-8 (no BOM) JSON array file, locking the target and recording history.

    Args:
        output: Destination JSON file path.
        rows: Number of objects to generate (at least 1).
        schema: Property schema mapping covering the 13 types in `FIELD_TYPES`.
        seed: Optional random seed for deterministic output.
        overwrite: Allow replacing an existing output file.
        dry_run: Report the planned output without locking, writing, or recording history.
        state_root: State directory for locks and run history. Defaults to `state_dir()`.

    Returns:
        Summary of the generated or planned JSON file.

    Raises:
        InputError: Invalid row count, invalid schema, link/junction path, directory path,
            or existing destination without `overwrite=True`.
        OperationError: Target lock contention or OS write error.
    """
    if rows < 1:
        msg = f"Row count must be at least 1, not {rows}."
        raise InputError(msg)
    if not str(output).strip():
        msg = "Output path must not be empty."
        raise InputError(msg)

    parsed_schema = parse_schema(schema)
    _check_no_links(output)
    resolved = output.resolve(strict=False)
    if resolved.is_dir():
        msg = f"Output path is a directory: {resolved}."
        raise InputError(msg)
    if resolved.exists() and not overwrite:
        msg = f"Output already exists: {resolved}. Pass overwrite=True to replace it."
        raise InputError(msg)

    field_names = tuple(parsed_schema.keys())
    if dry_run:
        return JsonGenResult(resolved, rows, field_names, seed, dry_run=True)

    started_at = datetime.now(UTC)
    history = HistoryStore(state_root=state_root)
    history_args: dict[str, object] = {
        "output": str(resolved),
        "rows": rows,
        "fields": list(field_names),
        "seed": seed,
        "overwrite": overwrite,
    }
    try:
        with target_lock(resolved, state_root=state_root):
            if resolved.exists() and not overwrite:
                msg = f"Output already exists: {resolved}. Pass overwrite=True to replace it."
                raise InputError(msg)
            rng = random.Random(seed)  # noqa: S311 - synthetic test data generator, not cryptographic
            items = [
                {name: _generate_value(spec, rng) for name, spec in parsed_schema.items()}
                for _ in range(rows)
            ]
            payload = (json.dumps(items, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_bytes(payload)
            except OSError as error:
                msg = f"Cannot write {resolved}: {_os_error_reason(error)}."
                raise OperationError(msg) from error
    except PytoolboxError as error:
        history.record(
            "json-gen",
            history_args,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            succeeded=0,
            failed=1,
            error=str(error),
        )
        raise

    history.record(
        "json-gen",
        history_args,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        succeeded=1,
    )
    return JsonGenResult(resolved, rows, field_names, seed, dry_run=False)
