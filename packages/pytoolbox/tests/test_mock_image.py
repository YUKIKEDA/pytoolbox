import errno
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import cast

import pytest
from PIL import Image

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError
from pytoolbox.locks import target_lock
from pytoolbox.results import ItemFailure, Progress
from pytoolbox.tools.dev import mock_image
from pytoolbox.tools.dev.mock_image import MockImageItem, MockImageResult, generate_mock_images


def _collect(
    values: Iterator[Progress | MockImageResult],
) -> tuple[list[Progress], MockImageResult]:
    progress: list[Progress] = []
    result: MockImageResult | None = None
    for value in values:
        if isinstance(value, Progress):
            progress.append(value)
        else:
            result = value
    assert result is not None
    return progress, result


def test_generate_mock_images_png_and_jpg_with_random_ratio_and_seed(tmp_path: Path) -> None:
    state = tmp_path / "state"
    png_dir = tmp_path / "png_out"

    progress, result = _collect(
        generate_mock_images(
            png_dir,
            200,
            120,
            3,
            image_format="png",
            seed=42,
            state_root=state,
        )
    )
    assert len(progress) == 3
    assert progress[-1] == Progress(3, 3, png_dir.resolve() / "mock_003.png")
    assert result.format == "png"
    assert not result.dry_run
    assert result.batch.succeeded == 3
    assert result.batch.failures == ()
    assert not result.batch.cancelled
    for index, item in enumerate(result.items, start=1):
        assert item.path == png_dir.resolve() / f"mock_{index:03d}.png"
        assert (item.width, item.height) == (200, 120)
        assert item.bytes == item.path.stat().st_size > 0
        with Image.open(item.path) as opened:
            assert opened.format == "PNG"
            assert opened.size == (200, 120)

    jpg_a = tmp_path / "jpg_a"
    jpg_b = tmp_path / "jpg_b"
    _, res_a = _collect(
        generate_mock_images(
            jpg_a,
            160,
            100,
            4,
            random_ratio=True,
            image_format="JPG",
            seed=7,
            state_root=state,
        )
    )
    _, res_b = _collect(
        generate_mock_images(
            jpg_b,
            160,
            100,
            4,
            random_ratio=True,
            image_format="jpg",
            seed=7,
            state_root=state,
        )
    )
    dims_a = [(item.width, item.height) for item in res_a.items]
    dims_b = [(item.width, item.height) for item in res_b.items]
    assert dims_a == dims_b
    for w, h in dims_a:
        assert 40 <= w <= 160
        assert 25 <= h <= 100
    with Image.open(res_a.items[0].path) as opened:
        assert opened.format == "JPEG"

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 3
    assert runs[0].tool == "mock-image"
    assert runs[0].succeeded == 4


def test_generate_mock_images_handles_tiny_and_non_seeded_runs(tmp_path: Path) -> None:
    state = tmp_path / "state"
    tiny_dir = tmp_path / "tiny"
    _, res = _collect(
        generate_mock_images(tiny_dir, 4, 4, 2, state_root=state),
    )
    assert res.batch.succeeded == 2
    assert res.seed is None
    with Image.open(res.items[0].path) as opened:
        assert opened.size == (4, 4)


def test_generate_mock_images_dry_run_skips_writes_locks_and_history(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out_dir = tmp_path / "dry"

    with target_lock(out_dir / "mock_001.png", state_root=state):
        progress, result = _collect(
            generate_mock_images(
                out_dir,
                320,
                240,
                2,
                random_ratio=True,
                seed=9,
                dry_run=True,
                state_root=state,
            )
        )

    assert len(progress) == 2
    assert result.dry_run
    assert result.batch.succeeded == 2
    assert result.items == (
        MockImageItem(
            out_dir.resolve() / "mock_001.png",
            result.items[0].width,
            result.items[0].height,
            0,
        ),
        MockImageItem(
            out_dir.resolve() / "mock_002.png",
            result.items[1].width,
            result.items[1].height,
            0,
        ),
    )
    assert not out_dir.exists()
    assert not HistoryStore(state_root=state).path.exists()


def test_generate_mock_images_validates_inputs(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="Width"):
        generate_mock_images(tmp_path / "a", 0, 10, 1)
    with pytest.raises(InputError, match="Height"):
        generate_mock_images(tmp_path / "a", 10, 0, 1)
    with pytest.raises(InputError, match="Count"):
        generate_mock_images(tmp_path / "a", 10, 10, 0)
    with pytest.raises(InputError, match="Output directory must not be empty"):
        generate_mock_images(Path("   "), 10, 10, 1)
    with pytest.raises(InputError, match="Unknown image format"):
        generate_mock_images(tmp_path / "a", 10, 10, 1, image_format="webp")

    existing_file = tmp_path / "file.txt"
    existing_file.write_text("x", "utf-8")
    with pytest.raises(InputError, match="not a directory"):
        generate_mock_images(existing_file, 10, 10, 1)

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link_dir"
    link_dir.symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(InputError, match="Links and junctions"):
        generate_mock_images(link_dir / "nested", 10, 10, 1)

    state = tmp_path / "state"
    out_dir = tmp_path / "existing"
    _collect(generate_mock_images(out_dir, 64, 64, 1, state_root=state))
    with pytest.raises(InputError, match="already exists"):
        generate_mock_images(out_dir, 64, 64, 1, state_root=state)

    _, overwritten = _collect(
        generate_mock_images(out_dir, 80, 50, 1, overwrite=True, state_root=state)
    )
    assert overwritten.batch.succeeded == 1
    with Image.open(out_dir / "mock_001.png") as opened:
        assert opened.size == (80, 50)


def test_generate_mock_images_lock_contention_toctou_and_mkdir_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    out_dir = tmp_path / "locked"

    with target_lock(out_dir.resolve() / "mock_002.png", state_root=state):
        stream = generate_mock_images(out_dir, 32, 32, 2, state_root=state)
        with pytest.raises(OperationError, match="Target is locked"):
            list(stream)

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"

    toctou_dir = tmp_path / "toctou"
    stream_toctou = generate_mock_images(toctou_dir, 32, 32, 1, state_root=state)
    toctou_dir.mkdir()
    (toctou_dir / "mock_001.png").write_bytes(b"race")
    with pytest.raises(InputError, match="already exists"):
        list(stream_toctou)

    cant_create = tmp_path / "cant_create"
    real_mkdir = Path.mkdir

    def fail_mkdir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if self == cant_create.resolve():
            raise OSError(errno.EACCES, "localized denied")
        real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    with pytest.raises(OperationError, match="access is denied"):
        list(generate_mock_images(cant_create, 32, 32, 1, state_root=state))


def test_generate_mock_images_item_failures_cancel_and_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    out_dir = tmp_path / "failures"
    out_dir.mkdir()
    target_dir = out_dir / "mock_001.png"
    target_dir.mkdir()
    real_target = tmp_path / "real.bin"
    real_target.write_bytes(b"x")
    link_target = out_dir / "mock_002.png"
    link_target.symlink_to(real_target)
    fail_write = out_dir / "mock_003.png"
    real_write_bytes = Path.write_bytes

    def fake_write_bytes(self: Path, data: bytes) -> int:
        if self == fail_write.resolve():
            raise OSError(errno.ENOSPC, "disk full")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", fake_write_bytes)
    progress, result = _collect(
        generate_mock_images(out_dir, 32, 32, 4, overwrite=True, state_root=state)
    )
    assert len(progress) == 4
    assert result.batch.succeeded == 1
    assert result.batch.failures == (
        ItemFailure(target_dir.resolve(), "the path is a directory"),
        ItemFailure(out_dir.resolve() / "mock_002.png", "target is a link or junction"),
        ItemFailure(fail_write.resolve(), "no space left on device"),
    )

    cancel_dir = tmp_path / "cancelled"
    checks = 0

    def stop_after_one() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    c_progress, c_result = _collect(
        generate_mock_images(cancel_dir, 32, 32, 3, state_root=state, cancel=stop_after_one)
    )
    assert len(c_progress) == 1
    assert c_result.batch.succeeded == 1
    assert c_result.batch.cancelled

    aborted_dir = tmp_path / "aborted"
    gen = generate_mock_images(aborted_dir, 32, 32, 3, state_root=state)
    first = next(gen)
    assert isinstance(first, Progress)
    cast("Generator[Progress | MockImageResult, None, None]", gen).close()
    latest = HistoryStore(state_root=state).list_runs()[0]
    assert latest.status == "cancelled"
    assert latest.succeeded == 1


def test_os_error_reason_fallback() -> None:
    assert mock_image._os_error_reason(OSError(errno.ENOLCK, "no locks")) == "no locks"
