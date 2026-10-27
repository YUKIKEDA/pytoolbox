from pathlib import Path

import pytest

from pytoolbox.errors import InputError
from pytoolbox.results import ItemFailure, Progress
from pytoolbox.tools.dev.hardcoding import (
    Finding,
    HardcodingResult,
    Kind,
    find_in_line,
    list_files,
    parse_extensions,
    parse_kinds,
    parse_names,
    scan_hardcoding,
)


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


def _hits(line: str, kinds: tuple[Kind, ...] = tuple(Kind)) -> list[tuple[Kind, str]]:
    return [(kind, text) for _, kind, text in find_in_line(line, kinds)]


def test_a_string_needs_the_same_quote_at_both_ends_and_three_characters() -> None:
    kinds = (Kind.STRING,)

    assert _hits('x = "abc"', kinds) == [(Kind.STRING, '"abc"')]
    assert _hits("x = 'abc'", kinds) == [(Kind.STRING, "'abc'")]
    assert _hits('x = "ab"', kinds) == []
    assert _hits('say("it\'s")', kinds) == [(Kind.STRING, '"it\'s"')]
    assert _hits("x = \"abc'", kinds) == []


def test_numbers_need_four_digits_and_ips_four_parts() -> None:
    assert _hits("port = 8080; id = 123") == [(Kind.NUMBER, "8080")]
    assert _hits("host = 10.0.0.1") == [(Kind.IP, "10.0.0.1")]


def test_urls_and_email_addresses() -> None:
    assert _hits("see https://example.com/a?b=1 or www.example.org") == [
        (Kind.URL, "https://example.com/a?b=1"),
        (Kind.URL, "www.example.org"),
    ]
    assert _hits("mail me@example.com") == [(Kind.EMAIL, "me@example.com")]


def test_one_spot_gives_one_finding_per_kind_by_position() -> None:
    assert _hits('u = "http://10.0.0.1:8080"') == [
        (Kind.STRING, '"http://10.0.0.1:8080"'),
        (Kind.URL, "http://10.0.0.1:8080"),
        (Kind.IP, "10.0.0.1"),
        (Kind.NUMBER, "8080"),
    ]


@pytest.mark.parametrize("prefix", ["#", "//", "/*", "*", "<!--"])
def test_comment_lines_are_skipped(prefix: str) -> None:
    assert _hits(f'   {prefix} "secret" 8080') == []


def test_parse_lists_accept_commas_spaces_and_optional_dots() -> None:
    assert parse_extensions([".CS, vb", "py"]) == {".cs", ".vb", ".py"}
    assert parse_names(["Bin, obj", ""]) == {"bin", "obj"}
    assert parse_kinds(["url,IP"]) == (Kind.IP, Kind.URL)


def test_parse_rejects_no_extension_no_kind_and_unknown_kinds() -> None:
    with pytest.raises(InputError, match="at least one extension"):
        parse_extensions([" , "])
    with pytest.raises(InputError, match="at least one kind"):
        parse_kinds([])
    with pytest.raises(InputError, match="Unknown kind: color"):
        parse_kinds(["url,color"])


def test_list_files_filters_by_extension_and_whole_folder_names(tmp_path: Path) -> None:
    for name in ["b.py", "a/c.CS", "a/d.txt", "bin/e.py", "Obj/f.py", "cabinet/g.py"]:
        _write(tmp_path / name, "")

    files = list_files(tmp_path, frozenset({".py", ".cs"}), frozenset({"bin", "obj"}))

    assert files == [Path("a/c.CS"), Path("b.py"), Path("cabinet/g.py")]


def test_list_files_does_not_follow_links(tmp_path: Path) -> None:
    outside = _write(tmp_path / "outside" / "x.py", "")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a symbolic link needs a privilege on this machine.")

    assert list_files(root, frozenset({".py"}), frozenset()) == []


def test_scan_yields_findings_then_progress_per_file_and_the_result_last(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", 'host = "10.0.0.1"\n# "skipped"\nport = 8080\n')
    _write(tmp_path / "b.py", "nothing\n")

    values = list(scan_hardcoding(tmp_path, kinds=["ip", "number"]))

    assert values == [
        Finding(Path("a.py"), 1, 8, Kind.IP, "10.0.0.1"),
        Finding(Path("a.py"), 3, 7, Kind.NUMBER, "8080"),
        Progress(1, 2, Path("a.py")),
        Progress(2, 2, Path("b.py")),
        HardcodingResult(2, {Kind.NUMBER: 1, Kind.IP: 1}),
    ]


def test_scan_reads_utf8_with_a_bom_and_cp932(tmp_path: Path) -> None:
    _write(tmp_path / "bom.cs", '﻿var s = "日本語";', "utf-8")
    _write(tmp_path / "sjis.vb", 'Dim s = "日本語"', "cp932")

    findings = [v for v in scan_hardcoding(tmp_path, kinds=["string"]) if isinstance(v, Finding)]

    assert [f.text for f in findings] == ['"日本語"', '"日本語"']


def test_scan_reports_a_file_it_cannot_decode_and_goes_on(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "")
    (tmp_path / "b.py").write_bytes(b"\x81\x7f\xff")
    _write(tmp_path / "c.py", "x = 12345")

    values = list(scan_hardcoding(tmp_path))

    assert values[-1] == HardcodingResult(
        3,
        dict.fromkeys(Kind, 0) | {Kind.NUMBER: 1},
        (ItemFailure(Path("b.py"), "Not UTF-8 or CP932."),),
    )


def test_scan_reports_a_file_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.py", "")

    def denied(_self: Path) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", denied)
    result = list(scan_hardcoding(tmp_path))[-1]

    assert isinstance(result, HardcodingResult)
    assert result.failures == (ItemFailure(Path("a.py"), "Cannot read: Permission denied."),)


def test_cancel_ends_with_what_was_found_so_far(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "x = 1234")
    _write(tmp_path / "b.py", "y = 5678")
    files_done = 0
    last: object = None

    def cancel() -> bool:
        return files_done >= 1

    for value in scan_hardcoding(tmp_path, kinds=["number"], cancel=cancel):
        if isinstance(value, Progress):
            files_done += 1
        last = value

    assert last == HardcodingResult(1, {Kind.NUMBER: 1}, cancelled=True)


def test_scan_checks_its_arguments_before_reading(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no such folder"):
        scan_hardcoding(tmp_path / "missing")
    with pytest.raises(InputError, match="no such folder"):
        scan_hardcoding(_write(tmp_path / "file.py", ""))
    with pytest.raises(InputError, match="Unknown kind"):
        scan_hardcoding(tmp_path, kinds=["nope"])
