"""Detection of hard-coded values in source files."""

import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pytoolbox.errors import InputError
from pytoolbox.results import Cancel, ItemFailure, Progress


class Kind(StrEnum):
    """What a finding is. The values are what the CLI prints and takes."""

    STRING = "string"
    NUMBER = "number"
    IP = "ip"
    URL = "url"
    EMAIL = "email"


PATTERNS: Mapping[Kind, re.Pattern[str]] = {
    Kind.STRING: re.compile(r""""[^"\n]{3,}"|'[^'\n]{3,}'"""),
    Kind.NUMBER: re.compile(r"\b\d{4,}\b"),
    Kind.IP: re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    Kind.URL: re.compile(r"""https?://[^\s<>"]+|www\.[^\s<>"]+"""),
    Kind.EMAIL: re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
}
"""The pattern of each kind (docs/design.ja.md §9)."""

COMMENT_PREFIXES = ("#", "//", "/*", "*", "<!--")
"""A line whose first non-space characters are one of these is a comment and is skipped."""

DEFAULT_EXTENSIONS = (".cs", ".vb", ".js", ".py")
DEFAULT_EXCLUDE = ("node_modules", "bin", "obj", ".git", ".venv")
KIND_NAMES = tuple(kind.value for kind in Kind)
"""Every kind name, in the order of `Kind`."""


@dataclass(frozen=True)
class Finding:
    """One hit of one kind.

    Attributes:
        path: The file, relative to the scanned folder.
        line: The line number, from 1.
        start: Where the hit starts in the line, in code points from 0.
        kind: What the hit is.
        text: The matched text, quotes included for a string.
    """

    path: Path
    line: int
    start: int
    kind: Kind
    text: str


@dataclass(frozen=True)
class HardcodingResult:
    """The outcome of a scan, yielded last.

    Attributes:
        files: How many files were scanned, unreadable ones included.
        counts: How many findings of each chosen kind.
        failures: The files that could not be read, relative to the scanned folder.
        cancelled: Whether the caller stopped the scan before the end.
    """

    files: int
    counts: Mapping[Kind, int]
    failures: tuple[ItemFailure, ...] = field(default=())
    cancelled: bool = False


def parse_extensions(values: Iterable[str]) -> frozenset[str]:
    """Turn extension lists into lower-case suffixes with a leading dot.

    Args:
        values: Lists separated by commas or spaces, such as `".cs, vb"`.

    Returns:
        The suffixes, such as `{".cs", ".vb"}`.

    Raises:
        InputError: No extension is given.
    """
    suffixes = {
        "." + item.lower().lstrip(".") for value in values for item in re.split(r"[,\s]+", value)
    } - {"."}
    if not suffixes:
        msg = "Name at least one extension."
        raise InputError(msg)
    return frozenset(suffixes)


def parse_names(values: Iterable[str]) -> frozenset[str]:
    """Turn folder-name lists into case-folded names.

    Args:
        values: Lists separated by commas or spaces, such as `"bin, obj"`.

    Returns:
        The names, case-folded. Empty when none is given.
    """
    return frozenset(
        item.casefold() for value in values for item in re.split(r"[,\s]+", value) if item
    )


def parse_kinds(values: Iterable[str]) -> tuple[Kind, ...]:
    """Turn kind lists into kinds, in the order of `Kind`.

    Args:
        values: Lists separated by commas or spaces, such as `"ip,url"`.

    Returns:
        The kinds.

    Raises:
        InputError: A name is not a kind, or no kind is given.
    """
    names = {item.lower() for value in values for item in re.split(r"[,\s]+", value) if item}
    if unknown := sorted(names - set(KIND_NAMES)):
        msg = f"Unknown kind: {', '.join(unknown)}. Use {', '.join(KIND_NAMES)}."
        raise InputError(msg)
    if not names:
        msg = "Choose at least one kind."
        raise InputError(msg)
    return tuple(kind for kind in Kind if kind.value in names)


def list_files(root: Path, extensions: frozenset[str], exclude: frozenset[str]) -> list[Path]:
    """List the files to scan under a folder, in path order.

    Links and junctions are not followed. A folder whose name, case-folded, is in `exclude`
    is skipped with everything under it. A folder that cannot be listed is skipped.

    Args:
        root: The folder.
        extensions: Lower-case suffixes with a leading dot.
        exclude: Case-folded folder names.

    Returns:
        The files, relative to `root`.
    """
    found: list[Path] = []
    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink() or entry.is_junction():
                continue
            if entry.is_dir():
                if entry.name.casefold() not in exclude:
                    pending.append(Path(entry.path))
            elif entry.is_file() and Path(entry.name).suffix.lower() in extensions:
                found.append(Path(entry.path).relative_to(root))
    return sorted(found, key=lambda path: path.parts)


def read_source(path: Path) -> str:
    """Read a source file as UTF-8, with or without a BOM, or else as CP932.

    Raises:
        UnicodeDecodeError: The file is neither.
        OSError: The file cannot be read.
    """
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp932")


def find_in_line(line: str, kinds: Iterable[Kind]) -> list[tuple[int, Kind, str]]:
    """Find the hits of the given kinds in one line, skipping a comment line.

    Args:
        line: The line, without its line break.
        kinds: The kinds to look for.

    Returns:
        Each hit's start, kind, and text, by start and then by the order of `Kind`.
    """
    if line.lstrip().startswith(COMMENT_PREFIXES):
        return []
    order = list(Kind)
    hits = [
        (match.start(), kind, match.group())
        for kind in kinds
        for match in PATTERNS[kind].finditer(line)
    ]
    return sorted(hits, key=lambda hit: (hit[0], order.index(hit[1])))


def scan_hardcoding(
    root: Path,
    *,
    extensions: Iterable[str] = DEFAULT_EXTENSIONS,
    exclude: Iterable[str] = DEFAULT_EXCLUDE,
    kinds: Iterable[str] = KIND_NAMES,
    cancel: Cancel | None = None,
) -> Iterator[Progress | Finding | HardcodingResult]:
    """Scan a folder for hard-coded values.

    The arguments are checked when this is called, before anything is read. The returned
    iterator lists the files first, then reads them one by one. It yields each finding as it
    is found, `Progress` after each file, and one `HardcodingResult` last.

    Args:
        root: The folder.
        extensions: Lists of file extensions, as `parse_extensions` takes them.
        exclude: Lists of folder names to skip, as `parse_names` takes them.
        kinds: Lists of kinds to find, as `parse_kinds` takes them.
        cancel: Checked between files. When it returns True, the scan ends with
            `cancelled=True` and what it found so far.

    Returns:
        The iterator.

    Raises:
        InputError: The folder does not exist or is not a folder, an extension list is
            empty, or a kind is unknown or none is given.
    """
    if not root.is_dir():
        msg = f"{root}: no such folder."
        raise InputError(msg)
    return _scan(
        root,
        parse_extensions(extensions),
        parse_names(exclude),
        parse_kinds(kinds),
        cancel or (lambda: False),
    )


def _scan(
    root: Path,
    extensions: frozenset[str],
    exclude: frozenset[str],
    kinds: tuple[Kind, ...],
    cancel: Cancel,
) -> Iterator[Progress | Finding | HardcodingResult]:
    files = list_files(root, extensions, exclude)
    counts = dict.fromkeys(kinds, 0)
    failures: list[ItemFailure] = []
    for completed, path in enumerate(files, start=1):
        if cancel():
            yield HardcodingResult(completed - 1, counts, tuple(failures), cancelled=True)
            return
        try:
            text = read_source(root / path)
        except UnicodeDecodeError:
            failures.append(ItemFailure(path, "Not UTF-8 or CP932."))
        except OSError as error:
            failures.append(ItemFailure(path, f"Cannot read: {error.strerror}."))
        else:
            for number, line in enumerate(text.splitlines(), start=1):
                for start, kind, found in find_in_line(line, kinds):
                    counts[kind] += 1
                    yield Finding(path, number, start, kind, found)
        yield Progress(completed, len(files), path)
    yield HardcodingResult(len(files), counts, tuple(failures))
