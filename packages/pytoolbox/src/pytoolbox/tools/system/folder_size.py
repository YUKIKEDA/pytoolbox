"""The size of each direct item of a folder."""

import csv
import io
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pytoolbox.errors import InputError
from pytoolbox.results import Cancel, Progress

_UNITS = ("B", "KB", "MB", "GB", "TB")
_STEP = 1024


class ItemKind(StrEnum):
    """What a direct item is. The values are what the CLI and the CSV print."""

    FOLDER = "folder"
    FILE = "file"
    LINK = "link"


@dataclass(frozen=True)
class SizeItem:
    """One direct item of the folder.

    Attributes:
        name: The item's name.
        kind: What it is. A link or junction is not followed.
        size: Bytes: a file's logical size, a folder's total, or 0 for a link.
    """

    name: str
    kind: ItemKind
    size: int


@dataclass(frozen=True)
class FolderSizeResult:
    """The outcome of a walk, yielded last.

    Attributes:
        items: The direct items, largest first, then by name.
        unreadable: How many items could not be read and were left out of the sizes.
        cancelled: Whether the caller stopped the walk. Then `items` holds the direct files,
            the links, and only the folders that were summed whole.
    """

    items: tuple[SizeItem, ...]
    unreadable: int = 0
    cancelled: bool = False

    @property
    def total(self) -> int:
        """The sum of the item sizes."""
        return sum(item.size for item in self.items)


def human_size(size: int) -> str:
    """Write a size in 1024 steps: `512 B`, `1.5 KB`, up to `TB`, with one decimal past `B`."""
    if size < _STEP:
        return f"{size} B"
    value = size / _STEP
    for unit in _UNITS[1:-1]:
        if value < _STEP:
            return f"{value:.1f} {unit}"
        value /= _STEP
    return f"{value:.1f} {_UNITS[-1]}"


def to_csv(items: Sequence[SizeItem]) -> str:
    """Write items as CSV: a BOM, the header `name,kind,bytes,size`, and CRLF line ends."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["name", "kind", "bytes", "size"])
    writer.writerows([item.name, item.kind, item.size, human_size(item.size)] for item in items)
    return "﻿" + out.getvalue()


def _is_link(entry: os.DirEntry[str]) -> bool:
    return entry.is_symlink() or entry.is_junction()


def _sum_tree(root: str, cancel: Cancel) -> tuple[int, int] | None:
    """Sum a folder's files without following links. `None` when cancelled midway."""
    total = 0
    unreadable = 0
    pending = [root]
    while pending:
        if cancel():
            return None
        try:
            entries = list(os.scandir(pending.pop()))
        except OSError:
            unreadable += 1
            continue
        for entry in entries:
            try:
                if _is_link(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                unreadable += 1
    return total, unreadable


def folder_size(
    root: Path, *, cancel: Cancel | None = None
) -> Iterator[Progress | FolderSizeResult]:
    """Size each direct file and folder of a folder.

    The folder is checked when this is called, before anything is read. The returned
    iterator lists the direct items, then sums each direct folder in turn and yields
    `Progress` after each one, and one `FolderSizeResult` last.

    Args:
        root: The folder.
        cancel: Checked between folders it lists. When it returns True, the walk ends with
            `cancelled=True` and the items it finished.

    Returns:
        The iterator.

    Raises:
        InputError: The folder does not exist or is not a folder.
    """
    if not root.is_dir():
        msg = f"{root}: no such folder."
        raise InputError(msg)
    return _walk(root, cancel or (lambda: False))


def _walk(root: Path, cancel: Cancel) -> Iterator[Progress | FolderSizeResult]:
    items: list[SizeItem] = []
    folders: list[os.DirEntry[str]] = []
    unreadable = 0
    for entry in os.scandir(root):
        try:
            if _is_link(entry):
                items.append(SizeItem(entry.name, ItemKind.LINK, 0))
            elif entry.is_dir(follow_symlinks=False):
                folders.append(entry)
            else:
                size = entry.stat(follow_symlinks=False).st_size
                items.append(SizeItem(entry.name, ItemKind.FILE, size))
        except OSError:
            unreadable += 1
    folders.sort(key=lambda entry: entry.name.casefold())
    cancelled = False
    for done, entry in enumerate(folders, start=1):
        summed = _sum_tree(entry.path, cancel)
        if summed is None:
            cancelled = True
            break
        size, missed = summed
        items.append(SizeItem(entry.name, ItemKind.FOLDER, size))
        unreadable += missed
        yield Progress(done, len(folders), Path(entry.name))
    items.sort(key=lambda item: (-item.size, item.name.casefold()))
    yield FolderSizeResult(tuple(items), unreadable, cancelled)
