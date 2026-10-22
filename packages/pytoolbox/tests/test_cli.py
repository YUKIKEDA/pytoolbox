import argparse
import importlib.abc
import io
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from types import ModuleType

import pytest

import pytoolbox_gui.cli
from pytoolbox import cli, registry
from pytoolbox.errors import OperationError
from pytoolbox.registry import System, Tool

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


def test_the_real_registry_lists_text_count_under_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out.startswith("Developer Utilities\n  Text\n    text-count  ")


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
