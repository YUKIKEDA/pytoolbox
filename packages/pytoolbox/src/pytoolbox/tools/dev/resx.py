"""Comparison of .NET resource (`.resx`) files."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pytoolbox.errors import InputError, OperationError

_DECLARED_ENCODING = re.compile(rb"""^\s*<\?xml[^>]*\bencoding\s*=\s*["']([^"']+)["']""")


class Status(StrEnum):
    """How a key compares between the two files. The values are what the CLI prints."""

    SAME = "same"
    DIFFER = "differ"
    ONLY1 = "only1"
    ONLY2 = "only2"


@dataclass(frozen=True)
class Entry:
    """One `data` name and its value in each file.

    Attributes:
        key: The `name` of the `data` element.
        value1: The value in the first file, or `None` when the key is not there.
        value2: The value in the second file, or `None` when the key is not there.
        status: How the two values compare.
    """

    key: str
    value1: str | None
    value2: str | None
    status: Status


def read_resx(path: Path) -> dict[str, str]:
    """Read the `data` names and values of a `.resx` file.

    The XML declaration and the BOM decide the encoding. Only the text of `<value>` is read;
    `<comment>`, `type`, and the other attributes are not. A missing or empty `<value>` is the
    empty string.

    Args:
        path: The file.

    Returns:
        Each `data` name and its value, in file order.

    Raises:
        InputError: The file is missing, is not XML, uses an encoding the parser cannot read,
            or has a `data` without `name` or a name that appears twice.
        OperationError: The file exists but cannot be read.
    """
    if not path.is_file():
        msg = f"{path}: no such file."
        raise InputError(msg)
    try:
        data = path.read_bytes()
    except OSError as error:
        msg = f"{path}: cannot read: {error.strerror}."
        raise OperationError(msg) from error
    try:
        root = ET.fromstring(data)  # noqa: S314 - the user's own file; expat resists entity expansion (ADR 0011)
    except ET.ParseError as error:
        msg = f"{path}: not valid XML: {error}."
        raise InputError(msg) from error
    except ValueError as error:
        declared = _DECLARED_ENCODING.match(data)
        name = declared.group(1).decode("ascii", "replace") if declared else "unknown"
        msg = f"{path}: the encoding {name} is not supported: {error}."
        raise InputError(msg) from error
    values: dict[str, str] = {}
    for element in root.iter("data"):
        key = element.get("name")
        if key is None:
            msg = f"{path}: a data element has no name."
            raise InputError(msg)
        if key in values:
            msg = f"{path}: the name {key!r} appears twice."
            raise InputError(msg)
        value = element.find("value")
        values[key] = (value.text or "") if value is not None else ""
    return values


def compare_resx(path1: Path, path2: Path) -> tuple[Entry, ...]:
    """Compare the `data` names and values of two `.resx` files.

    A key in both files is `SAME` when its values are exactly equal, case and spaces included,
    and `DIFFER` otherwise.

    Args:
        path1: The first file.
        path2: The second file.

    Returns:
        Every key of either file, sorted by code point.

    Raises:
        InputError: A file is missing or broken, as `read_resx` says.
        OperationError: A file exists but cannot be read.
    """
    first = read_resx(path1)
    second = read_resx(path2)
    entries: list[Entry] = []
    for key in sorted(first.keys() | second.keys()):
        value1 = first.get(key)
        value2 = second.get(key)
        if value2 is None:
            status = Status.ONLY1
        elif value1 is None:
            status = Status.ONLY2
        elif value1 == value2:
            status = Status.SAME
        else:
            status = Status.DIFFER
        entries.append(Entry(key, value1, value2, status))
    return tuple(entries)
