import argparse
import importlib.abc
import io
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

import pytest

import pytoolbox_gui.cli
from pytoolbox import cli, registry
from pytoolbox.errors import OperationError
from pytoolbox.registry import System, Tool
from pytoolbox.tools.dev.regex import Group, Match
from pytoolbox.tools.system import udp
from pytoolbox.tools.system.folder_size import FolderSizeResult

TOOLS = (
    Tool("text-count", System.DEVELOPER, "Text", "Count characters."),
    Tool("regex", System.DEVELOPER, "Text", "Match a pattern."),
    Tool("cache-clean", System.OPERATIONS, "Cleanup", "Delete named caches."),
)


class _HideGui(importlib.abc.MetaPathFinder):
    """Acts as if pytoolbox-gui were not installed."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        del path, target
        if fullname.partition(".")[0] == "pytoolbox_gui":
            msg = f"No module named {fullname!r}"
            raise ModuleNotFoundError(msg, name=fullname)
        return None


@pytest.fixture(autouse=True)
def no_real_server(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> None:
        pytest.fail("A test started the real GUI server.")

    monkeypatch.setattr(pytoolbox_gui.cli, "serve", refuse)


def test_format_list_indents_system_category_and_tool() -> None:
    assert cli.format_list(TOOLS) == "\n".join(
        [
            "Developer Utilities",
            "  Text",
            "    text-count  Count characters.",
            "    regex       Match a pattern.",
            "System Operations",
            "  Cleanup",
            "    cache-clean  Delete named caches.",
        ]
    )


def test_format_list_is_empty_without_tools() -> None:
    assert cli.format_list(()) == ""


def test_list_prints_the_registry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(registry, "TOOLS", TOOLS)

    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out == cli.format_list(TOOLS) + "\n"


def test_list_prints_nothing_for_an_empty_registry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(registry, "TOOLS", ())

    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out == ""


def test_unknown_command_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["nope"]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_missing_command_exits_1() -> None:
    assert cli.main([]) == 1


def test_gui_does_not_print_the_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_gui(_args: argparse.Namespace) -> int:
        return 0

    monkeypatch.setattr(cli, "_gui", no_gui)
    monkeypatch.setattr(registry, "TOOLS", TOOLS)

    assert cli.main(["gui"]) == 0
    assert capsys.readouterr().out == ""


def test_the_real_registry_lists_regex_and_text_count_under_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == ["Developer Utilities", "  Text"]
    assert lines[2].startswith("    regex       ")
    assert lines[3].startswith("    text-count  ")
    assert lines[4:7] == [
        "  Code",
        f"    resx-compare  {registry.RESX_COMPARE.summary}",
        f"    hardcoding    {registry.HARDCODING.summary}",
    ]
    assert lines[7:] == [
        "System Operations",
        "  Storage",
        f"    folder-size  {registry.FOLDER_SIZE.summary}",
        "  Network",
        f"    udp  {registry.UDP.summary}",
    ]


def test_hardcoding_prints_findings_and_reports_skipped_files_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text('url = "https://example.com"\n', "utf-8")
    (tmp_path / "b.js").write_bytes(b"\x81\x7f\xff")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "c.js").write_text("x = 1234", "utf-8")

    assert cli.main(["hardcoding", str(tmp_path), "--kind", "url"]) == 0
    captured = capsys.readouterr()
    assert captured.out == 'src/a.py:1  url  "https://example.com"\n'
    assert captured.err == "skipped: b.js: Not UTF-8 or CP932.\n"


def test_hardcoding_takes_extensions_and_excludes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "a.txt").write_text("x = 1234", "utf-8")
    (tmp_path / "drop").mkdir()
    (tmp_path / "drop" / "b.txt").write_text("y = 5678", "utf-8")

    args = ["hardcoding", str(tmp_path), "--ext", "txt", "--exclude", "drop", "--kind", "number"]
    assert cli.main(args) == 0
    assert capsys.readouterr().out == 'keep/a.txt:1  number  "1234"\n'


def _sized(root: Path) -> Path:
    (root / "big").mkdir(parents=True)
    (root / "big" / "a.bin").write_bytes(b"x" * 2048)
    (root / "small.txt").write_bytes(b"x" * 10)
    return root


def test_folder_size_prints_the_table_and_the_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["folder-size", str(_sized(tmp_path))]) == 0
    assert capsys.readouterr().out == (
        "2.0 KB  folder  big\n  10 B  file    small.txt\n\ntotal: 2.0 KB (2 items)\n"
    )


def test_folder_size_top_counts_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["folder-size", str(_sized(tmp_path)), "--top", "1"]) == 0
    assert capsys.readouterr().out.splitlines()[:2] == ["2.0 KB  folder  big", "... 1 more"]


def test_folder_size_csv_is_bytes_with_a_bom_and_crlf(
    tmp_path: Path, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    assert cli.main(["folder-size", str(_sized(tmp_path)), "--csv", "--top", "1"]) == 0
    assert capfdbinary.readouterr().out == (
        b"\xef\xbb\xbfname,kind,bytes,size\r\nbig,folder,2048,2.0 KB\r\nsmall.txt,file,10,10 B\r\n"
    )


def test_folder_size_rejects_a_top_below_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["folder-size", str(tmp_path), "--top", "0"]) == 1
    assert "--top must be 1 or more." in capsys.readouterr().err


def test_folder_size_shows_progress_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert cli.main(["folder-size", str(_sized(tmp_path))]) == 0
    err = capsys.readouterr().err
    assert "\rfolders 1/1" in err
    assert err.endswith("\r\033[K")


def test_format_sizes_reports_unreadable_and_cancelled() -> None:
    result = FolderSizeResult((), unreadable=2, cancelled=True)

    assert cli.format_sizes(result) == "total: 0 B (0 items)\nunreadable: 2\ncancelled"


class _OneDatagram:
    """A socket that receives one datagram and then has Ctrl+C pressed."""

    def __init__(self) -> None:
        self.calls = 0

    def bind(self, address: tuple[str, int], /) -> None:
        self.address = address

    def settimeout(self, value: float | None, /) -> None:
        self.timeout = value

    def recvfrom(self, bufsize: int, /) -> tuple[bytes, tuple[str, int]]:
        self.calls += 1
        if self.calls == 1:
            return b"Hi", ("127.0.0.1", 5000)
        raise KeyboardInterrupt

    def close(self) -> None:
        self.closed = True


def test_udp_logs_each_datagram_and_exits_130_on_ctrl_c(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sock = _OneDatagram()
    monkeypatch.setattr(udp, "udp_socket", lambda: sock)

    assert cli.main(["udp", "--port", "9100"]) == cli.INTERRUPTED
    captured = capsys.readouterr()
    assert captured.err == "listening on 0.0.0.0:9100 (Ctrl+C to stop)\n"
    assert captured.out.endswith('  from 127.0.0.1:5000  2 bytes  hex 4869  text "Hi"\n')
    assert sock.address == ("0.0.0.0", 9100)  # noqa: S104 - the address the tool listens on
    assert sock.closed


def test_udp_port_out_of_range_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["udp", "--port", "70000"]) == 1
    assert "outside 1 to 65535" in capsys.readouterr().err


def test_hardcoding_unknown_kind_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["hardcoding", str(tmp_path), "--kind", "color"]) == 1
    assert "Unknown kind: color" in capsys.readouterr().err


def _resx_file(path: Path, *pairs: tuple[str, str]) -> Path:
    body = "".join(f'<data name="{name}"><value>{value}</value></data>' for name, value in pairs)
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?><root>{body}</root>', "utf-8")
    return path


def test_resx_compare_prints_the_differences_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _resx_file(tmp_path / "1.resx", ("a", "Hi"), ("b", "same"), ("c", 'say "x"'))
    second = _resx_file(tmp_path / "2.resx", ("a", "日本"), ("b", "same"), ("d", ""))

    assert cli.main(["resx-compare", str(first), str(second)]) == 0
    assert capsys.readouterr().out == (
        'differ  "a"  "Hi"  "日本"\nonly1  "c"  "say \\"x\\""  null\nonly2  "d"  null  ""\n'
    )


def test_resx_compare_all_also_prints_the_matches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _resx_file(tmp_path / "1.resx", ("b", "same"))
    second = _resx_file(tmp_path / "2.resx", ("b", "same"))

    assert cli.main(["resx-compare", str(first), str(second)]) == 0
    assert capsys.readouterr().out == ""
    assert cli.main(["resx-compare", str(first), str(second), "--all"]) == 0
    assert capsys.readouterr().out == 'same  "b"  "same"  "same"\n'


def test_resx_compare_missing_file_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _resx_file(tmp_path / "1.resx")

    assert cli.main(["resx-compare", str(first), str(tmp_path / "none.resx")]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def _stdin(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))


def test_text_count_counts_the_argument(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["text-count", "ab c\u3000\u6f22\uff71"]) == 0
    assert capsys.readouterr().out == "total       7\nnon-space   5\nhalf-width  5\n"


def test_text_count_reads_standard_input_without_its_bom(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stdin(monkeypatch, "\ufeff\u3042a\n".encode())

    assert cli.main(["text-count"]) == 0
    assert capsys.readouterr().out == "total       3\nnon-space   2\nhalf-width  1\n"


def test_text_count_counts_an_empty_argument_not_standard_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stdin(monkeypatch, b"ignored")

    assert cli.main(["text-count", ""]) == 0
    assert capsys.readouterr().out.startswith("total       0\n")


def test_text_count_rejects_standard_input_that_is_not_utf8(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stdin(monkeypatch, b"\x82\xa0")

    assert cli.main(["text-count"]) == 1
    assert capsys.readouterr().err.startswith("error: Standard input is not UTF-8")


def test_format_matches_puts_groups_under_their_match() -> None:
    matches = (
        Match(0, 3, 'a"\n', (Group(1, "word", 0, 1, "a"), Group(2, None, None, None, None))),
        Match(4, 4, "", ()),
    )

    assert cli.format_matches(matches) == "\n".join(
        [
            '1  0-3  "a\\"\\n"',
            '  1 word  0-1  "a"',
            "  2  unmatched",
            '2  4-4  ""',
        ]
    )


def test_format_matches_is_empty_without_matches() -> None:
    assert cli.format_matches(()) == ""


def test_regex_prints_every_match_with_g(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["regex", "-g", r"(\d)", "a1b2"]) == 0
    assert capsys.readouterr().out == '1  1-2  "1"\n  1  1-2  "1"\n2  3-4  "2"\n  1  3-4  "2"\n'


def test_regex_flags_and_standard_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stdin(monkeypatch, "\ufeffa\nA\n".encode())

    assert cli.main(["regex", "--ignore-case", "--multiline", "^a$"]) == 0
    assert capsys.readouterr().out == '1  0-1  "a"\n'


def test_regex_without_a_match_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["regex", "z", "abc"]) == 0
    assert capsys.readouterr().out == ""


def test_regex_with_an_invalid_pattern_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["regex", "(", "x"]) == 1
    assert capsys.readouterr().err.startswith("error: Invalid pattern: ")


def test_ctrl_c_exits_130_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted(_args: argparse.Namespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_regex", interrupted)

    assert cli.main(["regex", "a", "a"]) == 130
    assert capsys.readouterr().err == ""


def test_run_writes_utf8_to_a_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp932", newline="\n")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "argv", ["pytoolbox", "regex", ".", "\u20ac"])

    with pytest.raises(SystemExit) as raised:
        cli.run()

    out.flush()
    assert raised.value.code == 0
    buffer = out.buffer
    assert isinstance(buffer, io.BytesIO)
    assert buffer.getvalue() == '1  0-1  "\u20ac"\n'.encode()


def test_duplicate_registry_names_stop_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "TOOLS", (*TOOLS, TOOLS[0]))

    with pytest.raises(ValueError, match="Duplicate tool name"):
        cli.main(["list"])


def test_gui_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(pytoolbox_gui.cli, "serve", lambda: calls.append("serve"))

    assert cli.main(["gui"]) == 0
    assert calls == ["serve"]


def test_gui_exits_2_when_the_port_is_in_use(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def busy() -> None:
        msg = "Port 8000 on 127.0.0.1 is in use."
        raise OperationError(msg)

    monkeypatch.setattr(pytoolbox_gui.cli, "serve", busy)

    assert cli.main(["gui"]) == 2
    assert "Port 8000" in capsys.readouterr().err


def test_gui_exits_2_with_the_install_command_when_the_package_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in [name for name in sys.modules if name.partition(".")[0] == "pytoolbox_gui"]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [_HideGui(), *sys.meta_path])

    assert cli.main(["gui"]) == 2
    assert capsys.readouterr().err == cli.GUI_MISSING + "\n"


def test_gui_reraises_a_missing_module_inside_the_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pytoolbox_gui.cli", None)

    with pytest.raises(ModuleNotFoundError):
        cli.main(["gui"])


def test_run_exits_with_the_code_of_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pytoolbox", "list"])

    with pytest.raises(SystemExit) as raised:
        cli.run()

    assert raised.value.code == 0
