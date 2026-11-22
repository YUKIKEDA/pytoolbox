import argparse
import importlib.abc
import io
import os
import sys
import time
from collections.abc import Iterator, Sequence
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

import pytest

import pytoolbox_gui.cli
from pytoolbox import cli, registry
from pytoolbox.db import HistoryStore
from pytoolbox.errors import OperationError
from pytoolbox.locks import target_lock
from pytoolbox.registry import System, Tool
from pytoolbox.results import BatchResult, ItemFailure
from pytoolbox.state import state_dir
from pytoolbox.tools.dev.regex import Group, Match
from pytoolbox.tools.system import network, udp
from pytoolbox.tools.system.cleanup import CacheCleanResult, CacheItem
from pytoolbox.tools.system.file_copy import FileCopyResult
from pytoolbox.tools.system.folder_size import FolderSizeResult
from pytoolbox.tools.system.network import PingResult, PortResult

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
    assert lines[:4] == [
        "Developer Utilities",
        "  Generators",
        f"    csv-gen   {registry.CSV_GEN.summary}",
        f"    json-gen  {registry.JSON_GEN.summary}",
    ]
    assert lines[4:7] == [
        "  Text",
        f"    regex       {registry.REGEX.summary}",
        f"    text-count  {registry.TEXT_COUNT.summary}",
    ]
    assert lines[7:10] == [
        "  Code",
        f"    resx-compare  {registry.RESX_COMPARE.summary}",
        f"    hardcoding    {registry.HARDCODING.summary}",
    ]
    assert lines[10:] == [
        "System Operations",
        "  Files",
        f"    file-copy  {registry.FILE_COPY.summary}",
        "  Storage",
        f"    folder-size  {registry.FOLDER_SIZE.summary}",
        "  Cleanup",
        f"    cache-clean  {registry.CACHE_CLEAN.summary}",
        "  Network",
        f"    port  {registry.PORT.summary}",
        f"    ping  {registry.PING.summary}",
        f"    udp   {registry.UDP.summary}",
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


def test_ping_prints_reachable_summary_and_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, float]] = []

    def fake_ping(host: str, *, timeout: float = 3.0) -> PingResult:
        seen.append((host, timeout))
        return PingResult(host, reachable=True, latency_ms=12.34, output="Reply from 127.0.0.1")

    monkeypatch.setattr(network, "ping_host", fake_ping)

    assert cli.main(["ping", "127.0.0.1", "--timeout", "1.5"]) == 0
    assert seen == [("127.0.0.1", 1.5)]
    assert capsys.readouterr().out == "127.0.0.1: reachable (12.3 ms)\nReply from 127.0.0.1\n"


def test_ping_prints_unreachable_and_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_ping(host: str, *, timeout: float = 3.0) -> PingResult:
        del timeout
        return PingResult(host, reachable=False, latency_ms=None, output="")

    monkeypatch.setattr(network, "ping_host", fake_ping)

    assert cli.main(["ping", "192.0.2.1"]) == 0
    assert capsys.readouterr().out == "192.0.2.1: unreachable\n"


def test_format_ping_handles_reachable_without_latency() -> None:
    assert cli.format_ping(PingResult("host", True, None, "ok")) == "host: reachable\nok"


def test_ping_exits_1_on_invalid_input_and_2_on_operation_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["ping", ""]) == 1
    assert "error:" in capsys.readouterr().err
    assert cli.main(["ping", "localhost", "--timeout", "0"]) == 1
    assert "error:" in capsys.readouterr().err

    def boom(host: str, *, timeout: float = 3.0) -> PingResult:
        del host, timeout
        msg = "Failed to run command 'ping'."
        raise OperationError(msg)

    monkeypatch.setattr(network, "ping_host", boom)

    assert cli.main(["ping", "localhost"]) == 2
    assert "Failed to run command" in capsys.readouterr().err


def test_port_prints_open_and_closed_and_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_open(host: str, port: int, *, timeout: float = 3.0) -> PortResult:
        del timeout
        return PortResult(host, port, True, 4.26, "open")

    monkeypatch.setattr(network, "check_port", fake_open)
    assert cli.main(["port", "example.com", "443", "--timeout", "2"]) == 0
    assert capsys.readouterr().out == "example.com:443  open  4.3 ms\n"

    def fake_closed(host: str, port: int, *, timeout: float = 3.0) -> PortResult:
        del timeout
        return PortResult(host, port, False, None, "connection refused")

    monkeypatch.setattr(network, "check_port", fake_closed)
    assert cli.main(["port", "example.com", "443"]) == 0
    assert capsys.readouterr().out == "example.com:443  closed  connection refused\n"


def test_format_port_handles_open_without_latency() -> None:
    assert cli.format_port(PortResult("example.com", 443, True, None, "open")) == (
        "example.com:443  open"
    )


def test_port_invalid_inputs_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["port", "example.com", "70000"]) == 1
    assert "outside 1 to 65535" in capsys.readouterr().err
    assert cli.main(["port", "", "443"]) == 1
    assert "error:" in capsys.readouterr().err


def test_csv_gen_dry_run_and_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LOCALAPPDATA", str(state))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    out = tmp_path / "out.csv"

    assert cli.main(["csv-gen", str(out), "--rows", "3", "--col", "name:text", "--dry-run"]) == 0
    assert f"dry-run: {out.resolve()} (3 rows)" in capsys.readouterr().out
    assert not out.exists()

    assert (
        cli.main(
            [
                "csv-gen",
                str(out),
                "--rows",
                "3",
                "--col",
                "name:text",
                "--col",
                "age:number",
                "--seed",
                "7",
            ]
        )
        == 0
    )
    assert f"wrote: {out.resolve()} (3 rows)" in capsys.readouterr().out
    assert out.read_bytes().startswith(b"\xef\xbb\xbfname,age\r\n")

    assert cli.main(["csv-gen", str(out), "--rows", "1", "--col", "name:text"]) == 1
    assert "already exists" in capsys.readouterr().err

    assert cli.main(["csv-gen", str(out), "--rows", "1", "--col", "name:text", "--overwrite"]) == 0
    assert f"wrote: {out.resolve()} (1 rows)" in capsys.readouterr().out


def _fake_cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    local = tmp_path / "local"
    home = tmp_path / "home"
    state = tmp_path / "state"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return local, home


def test_cache_clean_dry_run_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    local, _home = _fake_cache_env(tmp_path, monkeypatch)
    old_temp = local / "Temp" / "old.tmp"
    old_temp.parent.mkdir(parents=True)
    old_temp.write_bytes(b"12345")
    old_time = time.time() - 90000
    os.utime(old_temp, (old_time, old_time))

    fresh_temp = local / "Temp" / "fresh.tmp"
    fresh_temp.write_bytes(b"999")

    pip_file = local / "pip" / "Cache" / "pkg.whl"
    pip_file.parent.mkdir(parents=True)
    pip_file.write_bytes(b"abcdefgh")

    assert cli.main(["cache-clean", "temp", "pip"]) == 0
    dry_out = capsys.readouterr().out
    assert str(old_temp) in dry_out
    assert str(pip_file) in dry_out
    assert str(fresh_temp) not in dry_out
    assert "dry-run: 2 planned, 13 B (13 bytes)" in dry_out
    assert old_temp.exists()
    assert pip_file.exists()
    assert not HistoryStore(state_root=state_dir()).path.exists()

    assert cli.main(["cache-clean", "temp", "pip", "--dry-run"]) == 0
    assert "dry-run: 2 planned, 13 B (13 bytes)" in capsys.readouterr().out

    assert cli.main(["cache-clean", "temp", "pip", "--apply"]) == 0
    apply_out = capsys.readouterr().out
    assert "applied: 2 deleted, 13 B (13 bytes)" in apply_out
    assert not old_temp.exists()
    assert fresh_temp.exists()
    assert not pip_file.exists()
    assert (local / "pip" / "Cache").is_dir()
    runs = HistoryStore(state_root=state_dir()).list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "cache-clean"


def test_cache_clean_rejects_bad_arguments_and_lock_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    local, _home = _fake_cache_env(tmp_path, monkeypatch)

    assert cli.main(["cache-clean"]) == 1
    assert capsys.readouterr().err.startswith("error: ")

    assert cli.main(["cache-clean", "unknown"]) == 1
    assert "Unknown cache target: unknown" in capsys.readouterr().err

    assert cli.main(["cache-clean", "temp", "--dry-run", "--apply"]) == 1
    assert "not allowed with argument" in capsys.readouterr().err

    pip_root = local / "pip" / "Cache"
    pip_root.mkdir(parents=True)
    with target_lock(pip_root, state_root=state_dir()):
        assert cli.main(["cache-clean", "pip", "--apply"]) == 2
        assert "Target is locked by another run" in capsys.readouterr().err


def test_cache_clean_shows_terminal_progress_and_formats_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    local, _home = _fake_cache_env(tmp_path, monkeypatch)
    pip_file = local / "pip" / "Cache" / "a.whl"
    pip_file.parent.mkdir(parents=True)
    pip_file.write_bytes(b"xy")

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert cli.main(["cache-clean", "pip"]) == 0
    captured = capsys.readouterr()
    assert "\rcache-clean 1/1" in captured.err
    assert captured.err.endswith("\r\033[K")

    partial_result = CacheCleanResult(
        targets=("pip",),
        items=(CacheItem("pip", pip_file, 2),),
        total_bytes=2,
        dry_run=False,
        batch=BatchResult(1, (ItemFailure(Path("bad.whl"), "access is denied"),), cancelled=True),
    )
    formatted = cli.format_cache_clean(partial_result)
    assert "applied: 1 deleted, 2 B (2 bytes), 1 failed, cancelled" in formatted

    def fake_clean(_targets: Sequence[str], *, apply: bool = False) -> Iterator[CacheCleanResult]:
        del apply
        yield partial_result

    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    monkeypatch.setattr(cli, "clean_caches", fake_clean)
    assert cli.main(["cache-clean", "pip", "--apply"]) == 0
    assert capsys.readouterr().err == "failed: bad.whl: access is denied\n"


def test_json_gen_dry_run_and_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LOCALAPPDATA", str(state))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    out = tmp_path / "out.json"

    assert (
        cli.main(
            [
                "json-gen",
                str(out),
                "--rows",
                "2",
                "--col",
                "id:uuid",
                "--col",
                "name:string",
                "--dry-run",
            ]
        )
        == 0
    )
    assert f"dry-run: {out.resolve()} (2 rows)" in capsys.readouterr().out
    assert not out.exists()

    assert (
        cli.main(
            [
                "json-gen",
                str(out),
                "--rows",
                "2",
                "--schema",
                '{"id": "uuid", "role": {"type": "custom_list", "values": ["a"]}}',
                "--seed",
                "5",
            ]
        )
        == 0
    )
    assert f"wrote: {out.resolve()} (2 rows)" in capsys.readouterr().out
    assert not out.read_bytes().startswith(b"\xef\xbb\xbf")

    assert cli.main(["json-gen", str(out), "--rows", "1", "--col", "id:uuid"]) == 1
    assert "already exists" in capsys.readouterr().err

    assert cli.main(["json-gen", str(out), "--rows", "1", "--col", "id:uuid", "--overwrite"]) == 0
    assert f"wrote: {out.resolve()} (1 rows)" in capsys.readouterr().out


def test_file_copy_dry_run_copy_overwrite_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LOCALAPPDATA", str(state))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    src = tmp_path / "note.txt"
    src.write_text("hello", encoding="utf-8")
    dest = tmp_path / "copies"

    assert (
        cli.main(["file-copy", str(src), str(dest), "--count", "2", "--start", "3", "--dry-run"])
        == 0
    )
    dry_out = capsys.readouterr().out
    assert str(dest.resolve() / "note_003.txt") in dry_out
    assert str(dest.resolve() / "note_004.txt") in dry_out
    assert "dry-run: 2 planned" in dry_out
    assert not dest.exists()
    assert not HistoryStore(state_root=state_dir()).path.exists()

    assert (
        cli.main(
            [
                "file-copy",
                str(src),
                str(dest),
                "--count",
                "2",
                "--format",
                "item_{n}{suffix}",
                "--start",
                "5",
            ]
        )
        == 0
    )
    copy_out = capsys.readouterr().out
    assert "copied: 2 files" in copy_out
    assert (dest / "item_5.txt").read_text(encoding="utf-8") == "hello"
    assert (dest / "item_6.txt").read_text(encoding="utf-8") == "hello"
    runs = HistoryStore(state_root=state_dir()).list_runs()
    assert len(runs) == 1
    assert runs[0].tool == "file-copy"

    assert (
        cli.main(
            [
                "file-copy",
                str(src),
                str(dest),
                "--count",
                "1",
                "--format",
                "item_{n}{suffix}",
                "--start",
                "5",
            ]
        )
        == 1
    )
    assert "already exists" in capsys.readouterr().err

    src.write_text("updated", encoding="utf-8")
    assert (
        cli.main(
            [
                "file-copy",
                str(src),
                str(dest),
                "--count",
                "1",
                "--format",
                "item_{n}{suffix}",
                "--start",
                "5",
                "--overwrite",
            ]
        )
        == 0
    )
    assert (dest / "item_5.txt").read_text(encoding="utf-8") == "updated"

    assert cli.main(["file-copy", "", str(dest), "--count", "1"]) == 1
    assert "Source path must not be empty" in capsys.readouterr().err
    assert cli.main(["file-copy", str(src), "", "--count", "1"]) == 1
    assert "Destination folder must not be empty" in capsys.readouterr().err

    locked_target = dest.resolve() / "note_001.txt"
    with target_lock(locked_target, state_root=state_dir()):
        assert cli.main(["file-copy", str(src), str(dest), "--count", "1"]) == 2
        assert "Target is locked by another run" in capsys.readouterr().err


def test_file_copy_shows_terminal_progress_and_formats_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LOCALAPPDATA", str(state))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    dest = tmp_path / "out"

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert cli.main(["file-copy", str(src), str(dest), "--count", "1", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "\rfile-copy 1/1" in captured.err
    assert captured.err.endswith("\r\033[K")

    partial_result = FileCopyResult(
        source=src.resolve(),
        dest_dir=dest.resolve(),
        items=(dest / "a_001.txt",),
        dry_run=False,
        batch=BatchResult(
            1, (ItemFailure(dest / "a_002.txt", "access is denied"),), cancelled=True
        ),
    )
    formatted = cli.format_file_copy(partial_result)
    assert "copied: 1 files, 1 failed, cancelled" in formatted
    assert (
        cli.format_file_copy(
            FileCopyResult(
                source=src.resolve(),
                dest_dir=dest.resolve(),
                items=(),
                dry_run=False,
                batch=BatchResult(0, (ItemFailure(dest / "a_001.txt", "access is denied"),)),
            )
        )
        == "copied: 0 files, 1 failed"
    )

    def fake_copy(*_args: object, **_kwargs: object) -> Iterator[FileCopyResult]:
        yield partial_result

    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    monkeypatch.setattr(cli, "copy_file_batch", fake_copy)
    assert cli.main(["file-copy", str(src), str(dest), "--count", "2"]) == 0
    assert capsys.readouterr().err == f"failed: {dest / 'a_002.txt'}: access is denied\n"
