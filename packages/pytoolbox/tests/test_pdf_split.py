import errno
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import cast

import pytest
from pypdf import PdfReader, PdfWriter

from pytoolbox.db import HistoryStore
from pytoolbox.errors import InputError, OperationError
from pytoolbox.locks import target_lock
from pytoolbox.results import ItemFailure, Progress
from pytoolbox.tools.system import pdf_split
from pytoolbox.tools.system.pdf_split import PdfSplitResult, split_pdf


def _make_pdf(path: Path, pages: int = 3) -> Path:
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=200 + index * 20, height=300)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _collect(
    values: Iterator[Progress | PdfSplitResult],
) -> tuple[list[Progress], PdfSplitResult]:
    progress: list[Progress] = []
    result: PdfSplitResult | None = None
    for value in values:
        if isinstance(value, Progress):
            progress.append(value)
        else:
            result = value
    assert result is not None
    return progress, result


def test_split_pdf_writes_one_pdf_per_page_and_records_history(tmp_path: Path) -> None:
    state = tmp_path / "state"
    src = _make_pdf(tmp_path / "report.pdf", pages=3)
    out_dir = tmp_path / "pages"

    progress, result = _collect(split_pdf(src, out_dir, state_root=state))
    assert len(progress) == 3
    assert progress[-1] == Progress(3, 3, out_dir.resolve() / "page_3.pdf")
    assert not result.dry_run
    assert result.source == src.resolve()
    assert result.output_dir == out_dir.resolve()
    assert result.batch.succeeded == 3
    assert result.batch.failures == ()
    assert not result.batch.cancelled
    assert result.items == tuple(out_dir.resolve() / f"page_{i}.pdf" for i in range(1, 4))

    for idx, page_path in enumerate(result.items):
        reader = PdfReader(page_path)
        assert len(reader.pages) == 1
        assert float(reader.pages[0].mediabox.width) == 200 + idx * 20

    nested_dir = tmp_path / "nested"
    _, custom_res = _collect(
        split_pdf(src, nested_dir, fmt="sub/{stem}_{n:02d}.pdf", state_root=state)
    )
    assert custom_res.items[0] == nested_dir.resolve() / "sub" / "report_01.pdf"
    assert (nested_dir / "sub" / "report_03.pdf").is_file()

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 2
    assert runs[0].tool == "pdf-split"
    assert runs[0].succeeded == 3


def test_split_pdf_dry_run_skips_writes_locks_and_history(tmp_path: Path) -> None:
    state = tmp_path / "state"
    src = _make_pdf(tmp_path / "doc.pdf", pages=2)
    out_dir = tmp_path / "dry"

    with target_lock(out_dir.resolve() / "page_1.pdf", state_root=state):
        progress, result = _collect(split_pdf(src, out_dir, dry_run=True, state_root=state))

    assert len(progress) == 2
    assert result.dry_run
    assert result.batch.succeeded == 2
    assert result.items == (
        out_dir.resolve() / "page_1.pdf",
        out_dir.resolve() / "page_2.pdf",
    )
    assert not out_dir.exists()
    assert not HistoryStore(state_root=state).path.exists()


def test_split_pdf_validates_paths_formats_and_pdf_contents(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "valid.pdf", pages=2)
    out_dir = tmp_path / "out"

    with pytest.raises(InputError, match="Source PDF path must not be empty"):
        split_pdf(Path("   "), out_dir)
    with pytest.raises(InputError, match="Output directory must not be empty"):
        split_pdf(src, Path("   "))
    with pytest.raises(InputError, match="Format must not be empty"):
        split_pdf(src, out_dir, fmt="   ")
    with pytest.raises(InputError, match="Source PDF was not found"):
        split_pdf(tmp_path / "missing.pdf", out_dir)
    with pytest.raises(InputError, match="Source PDF is not a regular file"):
        split_pdf(tmp_path, out_dir)

    not_dir = tmp_path / "not_a_dir.txt"
    not_dir.write_text("x", "utf-8")
    with pytest.raises(InputError, match="Output directory is not a directory"):
        split_pdf(src, not_dir)

    for bad_fmt, pattern in [
        ("{unknown}.pdf", "Invalid filename format"),
        ("trailing/", "Invalid output name"),
        ("../escape_{n}.pdf", "escapes destination folder"),
        (str(tmp_path / "abs_{n}.pdf"), "escapes destination folder"),
        ("same.pdf", "duplicate output paths"),
    ]:
        with pytest.raises(InputError, match=pattern):
            split_pdf(src, out_dir, fmt=bad_fmt)

    with pytest.raises(InputError, match="must not overwrite the source file"):
        split_pdf(src, tmp_path, fmt="{name}")

    real_pdf = _make_pdf(tmp_path / "real.pdf", pages=1)
    link_pdf = tmp_path / "link.pdf"
    link_pdf.symlink_to(real_pdf)
    with pytest.raises(InputError, match="Links and junctions"):
        split_pdf(link_pdf, out_dir)

    real_sub = tmp_path / "real_sub"
    real_sub.mkdir()
    out_dir.mkdir()
    (out_dir / "linked_sub").symlink_to(real_sub, target_is_directory=True)
    with pytest.raises(InputError, match="Links and junctions"):
        split_pdf(src, out_dir, fmt="linked_sub/{n}.pdf")

    corrupt = tmp_path / "broken.pdf"
    corrupt.write_bytes(b"not a pdf")
    with pytest.raises(InputError, match="Invalid PDF file"):
        split_pdf(corrupt, out_dir)

    empty_pdf = tmp_path / "empty.pdf"
    empty_writer = PdfWriter()
    with empty_pdf.open("wb") as handle:
        empty_writer.write(handle)
    with pytest.raises(InputError, match="PDF has no pages"):
        split_pdf(empty_pdf, out_dir)

    encrypted_pdf = tmp_path / "secret.pdf"
    enc_writer = PdfWriter()
    enc_writer.add_blank_page(width=100, height=100)
    enc_writer.encrypt("password")
    with encrypted_pdf.open("wb") as handle:
        enc_writer.write(handle)
    with pytest.raises(InputError, match="Encrypted PDF files are not supported"):
        split_pdf(encrypted_pdf, out_dir)

    state = tmp_path / "state"
    fresh_out = tmp_path / "existing"
    _collect(split_pdf(src, fresh_out, state_root=state))
    with pytest.raises(InputError, match="already exists"):
        split_pdf(src, fresh_out, state_root=state)

    _, overwritten = _collect(split_pdf(src, fresh_out, overwrite=True, state_root=state))
    assert overwritten.batch.succeeded == 2


def test_split_pdf_read_mkdir_lock_and_toctou_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    src = _make_pdf(tmp_path / "src.pdf", pages=2)
    out_dir = tmp_path / "locked"

    with target_lock(out_dir.resolve() / "page_2.pdf", state_root=state):
        stream = split_pdf(src, out_dir, state_root=state)
        with pytest.raises(OperationError, match="Target is locked"):
            list(stream)

    runs = HistoryStore(state_root=state).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"

    toctou_dir = tmp_path / "toctou"
    stream_toctou = split_pdf(src, toctou_dir, state_root=state)
    toctou_dir.mkdir()
    (toctou_dir / "page_1.pdf").write_bytes(b"race")
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
        list(split_pdf(src, cant_create, state_root=state))

    real_read_bytes = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == src.resolve():
            raise OSError(errno.EACCES, "localized denied")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(OperationError, match="access is denied"):
        split_pdf(src, tmp_path / "other")


def test_split_pdf_item_failures_cancel_and_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    src = _make_pdf(tmp_path / "four.pdf", pages=4)
    out_dir = tmp_path / "failures"
    out_dir.mkdir()
    (out_dir / "page_1.pdf").mkdir()
    dummy_real = tmp_path / "dummy.pdf"
    dummy_real.write_bytes(b"x")
    (out_dir / "page_2.pdf").symlink_to(dummy_real)
    fail_page = out_dir.resolve() / "page_3.pdf"

    real_write_bytes = Path.write_bytes

    def fail_write(self: Path, data: bytes) -> int:
        if self == fail_page:
            raise OSError(errno.ENOSPC, "disk full")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    progress, result = _collect(split_pdf(src, out_dir, overwrite=True, state_root=state))
    assert len(progress) == 4
    assert result.batch.succeeded == 1
    assert result.batch.failures == (
        ItemFailure(out_dir.resolve() / "page_1.pdf", "the path is a directory"),
        ItemFailure(out_dir.resolve() / "page_2.pdf", "target is a link or junction"),
        ItemFailure(fail_page, "no space left on device"),
    )

    cancel_dir = tmp_path / "cancelled"
    checks = 0

    def stop_after_two() -> bool:
        nonlocal checks
        checks += 1
        return checks > 2

    c_prog, c_res = _collect(split_pdf(src, cancel_dir, state_root=state, cancel=stop_after_two))
    assert len(c_prog) == 2
    assert c_res.batch.succeeded == 2
    assert c_res.batch.cancelled

    aborted_dir = tmp_path / "aborted"
    gen = split_pdf(src, aborted_dir, state_root=state)
    first = next(gen)
    assert isinstance(first, Progress)
    cast("Generator[Progress | PdfSplitResult, None, None]", gen).close()
    latest = HistoryStore(state_root=state).list_runs()[0]
    assert latest.status == "cancelled"
    assert latest.succeeded == 1


def test_os_error_reason_fallback() -> None:
    assert pdf_split._os_error_reason(OSError(errno.ENOLCK, "no locks")) == "no locks"
