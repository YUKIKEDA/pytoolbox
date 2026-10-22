"""The `pytoolbox` command: one level, `pytoolbox <tool>`."""

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import NoReturn

from pytoolbox import registry
from pytoolbox.errors import InputError, OperationError
from pytoolbox.registry import Tool
from pytoolbox.tools.dev.text import TextCount, count_text

GUI_MISSING = (
    "The GUI is not installed. Install it with:\n"
    "  uv tool install ./packages/pytoolbox --with ./packages/pytoolbox-gui"
)


class _Parser(argparse.ArgumentParser):
    """An argument parser that reports a usage error as `InputError`."""

    def error(self, message: str) -> NoReturn:
        """Raise instead of exiting, so a usage error exits with 1."""
        raise InputError(message)


def format_list(tools: Sequence[Tool]) -> str:
    """Render tools grouped by system and category, as `pytoolbox list` prints them.

    Args:
        tools: The tools to list.

    Returns:
        One line per system, category, and tool, indented by level. Empty when there are no tools.
    """
    lines: list[str] = []
    for system, categories in registry.group(tools).items():
        lines.append(str(system))
        for category, members in categories.items():
            lines.append(f"  {category}")
            width = max(len(tool.name) for tool in members)
            lines.extend(f"    {tool.name:<{width}}  {tool.summary}" for tool in members)
    return "\n".join(lines)


def read_stdin() -> str:
    """Read standard input as UTF-8 text, without a leading BOM.

    Returns:
        The text.

    Raises:
        InputError: The input is not UTF-8.
    """
    try:
        return sys.stdin.buffer.read().decode("utf-8-sig")
    except UnicodeDecodeError as error:
        msg = f"Standard input is not UTF-8: {error.reason} at byte {error.start}."
        raise InputError(msg) from error


def format_text_count(count: TextCount) -> str:
    """Render the counts as `pytoolbox text-count` prints them, one label and number per line."""
    rows = [
        ("total", count.total),
        ("non-space", count.non_space),
        ("half-width", count.half_width),
    ]
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label:<{width}}  {number}" for label, number in rows)


def _list(_args: argparse.Namespace) -> int:
    text = format_list(registry.TOOLS)
    if text:
        print(text)
    return 0


def _text_count(args: argparse.Namespace) -> int:
    text: str | None = args.text
    print(format_text_count(count_text(read_stdin() if text is None else text)))
    return 0


def _gui(_args: argparse.Namespace) -> int:
    try:
        from pytoolbox_gui.cli import serve  # noqa: PLC0415 - the core runs without the GUI installed
    except ModuleNotFoundError as error:
        if error.name != "pytoolbox_gui":
            raise
        print(GUI_MISSING, file=sys.stderr)
        return 2
    serve()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its exit code.

    Args:
        argv: Arguments after the program name. `None` reads `sys.argv`.

    Returns:
        0 on success, 1 on `InputError`, 2 on `OperationError`.
    """
    parser = _Parser(prog="pytoolbox", description="Personal toolbox.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="<tool>")
    listing = commands.add_parser("list", help="List the tools by system and category.")
    listing.set_defaults(handler=_list)
    gui = commands.add_parser("gui", help="Start the GUI on 127.0.0.1:8000 and open a browser.")
    gui.set_defaults(handler=_gui)
    text_count = commands.add_parser(registry.TEXT_COUNT.name, help=registry.TEXT_COUNT.summary)
    text_count.add_argument("text", nargs="?", help="The text. Omit it to read standard input.")
    text_count.set_defaults(handler=_text_count)
    registry.check_unique(registry.TOOLS)
    try:
        args = parser.parse_args(argv)
        handler: Callable[[argparse.Namespace], int] = args.handler
        return handler(args)
    except InputError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OperationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def run() -> NoReturn:
    """Entry point of the `pytoolbox` script."""
    sys.exit(main())
