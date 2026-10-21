"""The `pytoolbox` command: one level, `pytoolbox <tool>`."""

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn

from pytoolbox import registry
from pytoolbox.errors import InputError, OperationError
from pytoolbox.registry import Tool

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


def _gui() -> int:
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
    commands.add_parser("list", help="List the tools by system and category.")
    commands.add_parser("gui", help="Start the GUI on 127.0.0.1:8000 and open a browser.")
    registry.check_unique(registry.TOOLS)
    try:
        args = parser.parse_args(argv)
        if args.command == "gui":
            return _gui()
        text = format_list(registry.TOOLS)
        if text:
            print(text)
    except InputError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OperationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


def run() -> NoReturn:
    """Entry point of the `pytoolbox` script."""
    sys.exit(main())
