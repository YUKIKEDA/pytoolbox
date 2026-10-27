import os
from pathlib import Path

import pytest

from pytoolbox.errors import InputError
from pytoolbox.results import Progress
from pytoolbox.tools.system import folder_size as folder_size_module
from pytoolbox.tools.system.folder_size import (
    FolderSizeResult,
    ItemKind,
    SizeItem,
    folder_size,
    human_size,
    to_csv,
)


def _file(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (5 * 1024**2, "5.0 MB"),
        (3 * 1024**3, "3.0 GB"),
        (2 * 1024**4, "2.0 TB"),
        (2048 * 1024**4, "2048.0 TB"),
    ],
)
def test_human_size_steps_by_1024(size: int, text: str) -> None:
    assert human_size(size) == text


def test_to_csv_has_a_bom_the_header_and_crlf() -> None:
    items = [SizeItem("a,b", ItemKind.FOLDER, 2048), SizeItem("c", ItemKind.FILE, 1)]

    assert to_csv(items) == ('﻿name,kind,bytes,size\r\n"a,b",folder,2048,2.0 KB\r\nc,file,1,1 B\r\n')


def test_folders_sum_their_contents_and_items_go_largest_first(tmp_path: Path) -> None:
    _file(tmp_path / "small.txt", 10)
    _file(tmp_path / "big" / "a.bin", 300)
    _file(tmp_path / "big" / "deep" / "b.bin", 200)
    _file(tmp_path / "mid" / "c.bin", 100)
    (tmp_path / "empty").mkdir()

    values = list(folder_size(tmp_path))

    assert values == [
        Progress(1, 3, Path("big")),
        Progress(2, 3, Path("empty")),
        Progress(3, 3, Path("mid")),
        FolderSizeResult(
            (
                SizeItem("big", ItemKind.FOLDER, 500),
                SizeItem("mid", ItemKind.FOLDER, 100),
                SizeItem("small.txt", ItemKind.FILE, 10),
                SizeItem("empty", ItemKind.FOLDER, 0),
            )
        ),
    ]
    result = values[-1]
    assert isinstance(result, FolderSizeResult)
    assert result.total == 610


def test_a_direct_link_is_a_zero_row_and_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _file(outside / "x.bin", 100)
    root = tmp_path / "root"
    _file(root / "inner" / "y.bin", 5)
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
        (root / "inner" / "nested").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a symbolic link needs a privilege on this machine.")

    result = list(folder_size(root))[-1]

    assert isinstance(result, FolderSizeResult)
    assert result.items == (
        SizeItem("inner", ItemKind.FOLDER, 5),
        SizeItem("link", ItemKind.LINK, 0),
    )


def test_unreadable_items_are_counted_and_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _file(tmp_path / "ok" / "a.bin", 7)
    locked = tmp_path / "locked"
    locked.mkdir()
    real_scandir = os.scandir

    def scandir(path: str | os.PathLike[str]) -> object:
        if Path(path) == locked:
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    result = list(folder_size(tmp_path))[-1]

    assert result == FolderSizeResult(
        (SizeItem("ok", ItemKind.FOLDER, 7), SizeItem("locked", ItemKind.FOLDER, 0)), 1
    )


def test_a_direct_item_that_cannot_be_read_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _file(tmp_path / "a.bin", 3)
    _file(tmp_path / "sub" / "b.bin", 4)

    def is_link(entry: os.DirEntry[str]) -> bool:
        if entry.name in {"a.bin", "b.bin"}:
            raise PermissionError(13, "Permission denied")
        return False

    monkeypatch.setattr(folder_size_module, "_is_link", is_link)
    result = list(folder_size(tmp_path))[-1]

    assert result == FolderSizeResult((SizeItem("sub", ItemKind.FOLDER, 0),), 2)


def test_cancel_keeps_only_the_folders_summed_whole(tmp_path: Path) -> None:
    _file(tmp_path / "file.txt", 1)
    _file(tmp_path / "a" / "x.bin", 10)
    _file(tmp_path / "b" / "y.bin", 20)
    folders_done = 0

    def cancel() -> bool:
        return folders_done >= 1

    last: object = None
    for value in folder_size(tmp_path, cancel=cancel):
        if isinstance(value, Progress):
            folders_done += 1
        last = value

    assert last == FolderSizeResult(
        (SizeItem("a", ItemKind.FOLDER, 10), SizeItem("file.txt", ItemKind.FILE, 1)),
        cancelled=True,
    )


def test_a_missing_folder_or_a_file_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no such folder"):
        folder_size(tmp_path / "missing")
    with pytest.raises(InputError, match="no such folder"):
        folder_size(_file(tmp_path / "f.txt", 1))
