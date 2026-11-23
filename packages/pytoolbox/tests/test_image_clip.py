import errno
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import cast

import pytest
from PIL import Image

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError
from pytoolbox.locks import target_lock
from pytoolbox.results import Progress
from pytoolbox.tools.system import image_clip
from pytoolbox.tools.system.image_clip import (
    ClipBox,
    ClippedImageItem,
    ImageClipResult,
    clamp_box,
    clip_images,
    first_preview_image,
    parse_box,
)


def _collect(
    values: Iterator[Progress | ImageClipResult],
) -> tuple[list[Progress], ImageClipResult]:
    progress: list[Progress] = []
    result: ImageClipResult | None = None
    for value in values:
        if isinstance(value, Progress):
            progress.append(value)
        else:
            result = value
    assert result is not None
    return progress, result


def test_parse_box_and_clamp_box() -> None:
    assert parse_box(" 10, 20 , 100 , 80 ") == ClipBox(10, 20, 100, 80)
    assert parse_box("-10,-5,50,40") == ClipBox(-10, -5, 50, 40)

    for bad in (
        "10,20,30",
        "10,20,30,40,50",
        "10,a,30,40",
        "10,20,10,40",
        "10,20,30,20",
        "-5,0,0,10",
        "0,-5,10,0",
    ):
        with pytest.raises(InputError):
            parse_box(bad)

    assert clamp_box(ClipBox(-5, -10, 150, 120), 100, 80) == ClipBox(0, 0, 100, 80)
    assert clamp_box(ClipBox(10, 20, 50, 60), 100, 80) == ClipBox(10, 20, 50, 60)
    assert clamp_box(ClipBox(100, 0, 150, 50), 100, 80) is None
    assert clamp_box(ClipBox(0, 80, 50, 120), 100, 80) is None


def test_clip_images_across_formats_same_and_custom_output_dir(tmp_path: Path) -> None:
    state = tmp_path / "state"
    in_dir = tmp_path / "in"
    in_dir.mkdir()

    Image.new("RGBA", (100, 80), color=(200, 50, 50, 128)).save(in_dir / "a.jpg", format="PNG")
    Image.new("L", (60, 50), color=120).save(in_dir / "b.jpeg", format="JPEG")
    Image.new("RGBA", (80, 70), color=(10, 200, 30, 255)).save(in_dir / "c.png", format="PNG")
    Image.new("RGB", (40, 30), color=(10, 20, 200)).save(in_dir / "d.bmp", format="BMP")
    Image.new("RGB", (50, 50), color=(150, 150, 0)).save(in_dir / "e.gif", format="GIF")
    (in_dir / "notes.txt").write_text("ignore", "utf-8")
    (in_dir / "subdir").mkdir()

    real_extra = tmp_path / "extra.png"
    Image.new("RGB", (20, 20)).save(real_extra, format="PNG")
    (in_dir / "link.png").symlink_to(real_extra)

    box = ClipBox(-10, 5, 50, 45)
    progress, result = _collect(clip_images(in_dir, box, state_root=state))

    assert len(progress) == 5
    assert result.input_dir == in_dir.resolve()
    assert result.output_dir == in_dir.resolve()
    assert not result.dry_run
    assert result.batch.succeeded == 5
    assert result.batch.failures == ()
    assert not result.batch.cancelled

    expected_sizes = {
        "cropped_a.jpg": (50, 40, "JPEG"),
        "cropped_b.jpeg": (50, 40, "JPEG"),
        "cropped_c.png": (50, 40, "PNG"),
        "cropped_d.bmp": (40, 25, "BMP"),
        "cropped_e.gif": (50, 40, "GIF"),
    }
    for item in result.items:
        name = item.output.name
        assert name in expected_sizes
        exp_w, exp_h, exp_fmt = expected_sizes[name]
        with Image.open(item.output) as opened:
            assert opened.size == (exp_w, exp_h)
            assert opened.format == exp_fmt

    # Re-running into the same directory skips existing cropped_* files from discovery;
    # with overwrite=True it re-crops only the 5 original files.
    _, rerun = _collect(
        clip_images(in_dir, ClipBox(0, 0, 20, 20), overwrite=True, state_root=state)
    )
    assert rerun.batch.succeeded == 5
    assert all(not item.source.name.startswith("cropped_") for item in rerun.items)

    # Custom output_dir does not skip files named cropped_* in input_dir if explicitly placed,
    # or writes to the separate directory cleanly.
    out_dir = tmp_path / "custom_out"
    _, custom_res = _collect(
        clip_images(in_dir, ClipBox(0, 0, 15, 15), output_dir=out_dir, state_root=state)
    )
    assert custom_res.output_dir == out_dir.resolve()
    # Both original 5 and cropped_ 5 are candidates when output_dir != input_dir.
    assert custom_res.batch.succeeded == 10

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 3
    assert runs[0].tool == "image-clip"


def test_first_preview_image_and_errors(tmp_path: Path) -> None:
    in_dir = tmp_path / "preview_dir"
    in_dir.mkdir()
    (in_dir / "01_broken.png").write_bytes(b"not an image")
    Image.new("CMYK", (32, 24), color=(10, 20, 30, 40)).save(in_dir / "02_cmyk.jpg", format="JPEG")
    Image.new("RGB", (16, 12), color=(10, 20, 30)).save(in_dir / "03_rgb.png", format="PNG")

    path, w, h, uri = first_preview_image(in_dir)
    assert path == (in_dir / "02_cmyk.jpg").resolve()
    assert (w, h) == (32, 24)
    assert uri.startswith("data:image/jpeg;base64,")

    (in_dir / "02_cmyk.jpg").unlink()
    path2, w2, h2, uri2 = first_preview_image(in_dir)
    assert path2 == (in_dir / "03_rgb.png").resolve()
    assert (w2, h2) == (16, 12)
    assert uri2.startswith("data:image/png;base64,")

    (in_dir / "03_rgb.png").unlink()
    with pytest.raises(InputError, match="No readable preview image"):
        first_preview_image(in_dir)


def test_clip_images_dry_run_skips_writes_locks_and_history(tmp_path: Path) -> None:
    state = tmp_path / "state"
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    src = in_dir / "pic.png"
    Image.new("RGB", (60, 40)).save(src, format="PNG")
    out_dir = tmp_path / "dry_out"
    target = out_dir.resolve() / "cropped_pic.png"

    with target_lock(target, state_root=state):
        progress, result = _collect(
            clip_images(
                in_dir,
                ClipBox(10, 5, 80, 50),
                output_dir=out_dir,
                dry_run=True,
                state_root=state,
            )
        )

    assert len(progress) == 1
    assert result.dry_run
    assert result.items == (ClippedImageItem(src.resolve(), target, ClipBox(10, 5, 60, 40)),)
    assert not out_dir.exists()
    assert not HistoryStore(state_root=state).path.exists()


def test_clip_images_validates_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_dir = tmp_path / "in"
    in_dir.mkdir()

    with pytest.raises(InputError, match="No supported images"):
        clip_images(in_dir, ClipBox(0, 0, 10, 10))

    Image.new("RGB", (20, 20)).save(in_dir / "a.png", format="PNG")

    with pytest.raises(InputError, match="Input directory must not be empty"):
        clip_images(Path("   "), ClipBox(0, 0, 10, 10))
    with pytest.raises(InputError, match="Input directory does not exist"):
        clip_images(tmp_path / "missing", ClipBox(0, 0, 10, 10))
    with pytest.raises(InputError, match="Output directory must not be empty"):
        clip_images(in_dir, ClipBox(0, 0, 10, 10), output_dir=Path("  "))
    with pytest.raises(InputError, match="Crop box must have x1 < x2"):
        clip_images(in_dir, ClipBox(10, 0, 10, 10))
    with pytest.raises(InputError, match="Crop box x2 and y2 must be positive"):
        clip_images(in_dir, ClipBox(-10, -10, 0, 5))

    file_path = tmp_path / "file.txt"
    file_path.write_text("x", "utf-8")
    with pytest.raises(InputError, match="Input directory is not a directory"):
        clip_images(file_path, ClipBox(0, 0, 10, 10))
    with pytest.raises(InputError, match="Output directory is not a directory"):
        clip_images(in_dir, ClipBox(0, 0, 10, 10), output_dir=file_path)

    link_dir = tmp_path / "link_dir"
    link_dir.symlink_to(in_dir, target_is_directory=True)
    with pytest.raises(InputError, match="Links and junctions"):
        clip_images(link_dir, ClipBox(0, 0, 10, 10))
    with pytest.raises(InputError, match="Links and junctions"):
        clip_images(in_dir, ClipBox(0, 0, 10, 10), output_dir=link_dir / "out")

    state = tmp_path / "state"
    _collect(clip_images(in_dir, ClipBox(0, 0, 10, 10), state_root=state))
    with pytest.raises(InputError, match="already exists"):
        clip_images(in_dir, ClipBox(0, 0, 10, 10), state_root=state)

    real_iterdir = Path.iterdir

    def fail_iterdir(self: Path) -> Iterator[Path]:
        if self == in_dir.resolve():
            raise OSError(errno.EACCES, "denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)
    with pytest.raises(InputError, match="Cannot read input directory"):
        clip_images(in_dir, ClipBox(0, 0, 10, 10), overwrite=True)


def test_clip_images_lock_contention_toctou_and_mkdir_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    Image.new("RGB", (20, 20)).save(in_dir / "a.png", format="PNG")

    out_dir = tmp_path / "locked_out"
    with target_lock(out_dir.resolve() / "cropped_a.png", state_root=state):
        stream = clip_images(in_dir, ClipBox(0, 0, 10, 10), output_dir=out_dir, state_root=state)
        with pytest.raises(OperationError, match="Target is locked"):
            list(stream)

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"

    toctou_out = tmp_path / "toctou_out"
    stream_toctou = clip_images(
        in_dir, ClipBox(0, 0, 10, 10), output_dir=toctou_out, state_root=state
    )
    toctou_out.mkdir()
    (toctou_out / "cropped_a.png").write_bytes(b"race")
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
        list(clip_images(in_dir, ClipBox(0, 0, 10, 10), output_dir=cant_create, state_root=state))


def test_clip_images_item_failures_cancel_and_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # 1. Corrupt source image
    corrupt_src = in_dir / "01_corrupt.png"
    corrupt_src.write_bytes(b"not an image")

    # 2. Image smaller than box offset -> clamped box is None
    tiny_src = in_dir / "02_tiny.png"
    Image.new("RGB", (10, 10)).save(tiny_src, format="PNG")

    # 3. Target is a directory
    dir_src = in_dir / "03_dir.png"
    Image.new("RGB", (50, 50)).save(dir_src, format="PNG")
    target_dir = out_dir / "cropped_03_dir.png"
    target_dir.mkdir()

    # 4. Target is a symlink
    link_src = in_dir / "04_link.png"
    Image.new("RGB", (50, 50)).save(link_src, format="PNG")
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"x")
    target_link = out_dir / "cropped_04_link.png"
    target_link.symlink_to(dummy)

    # 5. Save raises OSError
    fail_src = in_dir / "05_savefail.png"
    Image.new("RGB", (50, 50)).save(fail_src, format="PNG")
    target_fail = out_dir / "cropped_05_savefail.png"

    # 6. Good image
    ok_src = in_dir / "06_ok.png"
    Image.new("RGB", (50, 50)).save(ok_src, format="PNG")

    real_save = image_clip._save_cropped

    def fake_save(cropped: Image.Image, target: Path) -> None:
        if target == target_fail.resolve():
            raise OSError(errno.ENOSPC, "disk full")
        real_save(cropped, target)

    monkeypatch.setattr(image_clip, "_save_cropped", fake_save)

    progress, result = _collect(
        clip_images(
            in_dir,
            ClipBox(20, 20, 40, 40),
            output_dir=out_dir,
            overwrite=True,
            state_root=state,
        )
    )
    assert len(progress) == 6
    assert result.batch.succeeded == 1
    assert len(result.batch.failures) == 5
    reasons = [f.message for f in result.batch.failures]
    assert any("outside image bounds (10x10)" in r for r in reasons)
    assert "the path is a directory" in reasons
    assert "target is a link or junction" in reasons
    assert "no space left on device" in reasons

    # Cancellation after 1 item
    cancel_out = tmp_path / "cancel_out"
    checks = 0

    def stop_after_one() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    c_prog, c_res = _collect(
        clip_images(
            in_dir,
            ClipBox(0, 0, 10, 10),
            output_dir=cancel_out,
            state_root=state,
            cancel=stop_after_one,
        )
    )
    assert len(c_prog) == 1
    assert c_res.batch.cancelled

    # GeneratorExit records cancelled in history
    abort_out = tmp_path / "abort_out"
    gen = clip_images(in_dir, ClipBox(0, 0, 10, 10), output_dir=abort_out, state_root=state)
    first = next(gen)
    assert isinstance(first, Progress)
    cast("Generator[Progress | ImageClipResult, None, None]", gen).close()
    latest = HistoryStore(state_root=state).list_runs()[0]
    assert latest.status == "cancelled"


def test_os_error_reason_fallback() -> None:
    assert image_clip._os_error_reason(OSError(errno.ENOLCK, "no locks")) == "no locks"
