"""The `pytoolbox` command: one level, `pytoolbox <tool>`."""

import argparse
import io
import json
import sys
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import NoReturn

from pytoolbox import registry
from pytoolbox.errors import InputError, OperationError
from pytoolbox.registry import Tool
from pytoolbox.tools.dev.hardcoding import (
    DEFAULT_EXCLUDE,
    DEFAULT_EXTENSIONS,
    KIND_NAMES,
    Finding,
    HardcodingResult,
    scan_hardcoding,
)
from pytoolbox.tools.dev.regex import Group, Match, match_pattern
from pytoolbox.tools.dev.resx import Entry, Status, compare_resx
from pytoolbox.tools.dev.text import TextCount, count_text
from pytoolbox.tools.system.folder_size import (
    FolderSizeResult,
    SizeItem,
    folder_size,
    human_size,
    to_csv,
)
from pytoolbox.worker import Worker

INTERRUPTED = 130
"""The exit code after Ctrl+C: 128 plus `SIGINT`."""

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


def _quoted(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _group_line(group: Group) -> str:
    label = f"{group.number} {group.name}" if group.name is not None else str(group.number)
    if group.text is None:
        return f"  {label}  unmatched"
    return f"  {label}  {group.start}-{group.end}  {_quoted(group.text)}"


def format_matches(matches: Sequence[Match]) -> str:
    """Render matches as `pytoolbox regex` prints them.

    Each match is one line: its number from 1, `start-end`, and the matched text as a JSON
    string. Each group follows on its own line, indented by two spaces: its number, its name if
    it has one, and `start-end` and the text, or `unmatched` when it took no part in the match.

    Args:
        matches: The matches.

    Returns:
        The lines. Empty when there are no matches.
    """
    lines: list[str] = []
    for index, match in enumerate(matches, start=1):
        lines.append(f"{index}  {match.start}-{match.end}  {_quoted(match.text)}")
        lines.extend(_group_line(group) for group in match.groups)
    return "\n".join(lines)


def format_resx(entries: Sequence[Entry], *, show_same: bool = False) -> str:
    """Render a comparison as `pytoolbox resx-compare` prints it.

    Each key is one line: its status, the key, and the two values, separated by two spaces.
    The key and the values are JSON strings, and a value missing from its file is `null`.

    Args:
        entries: The comparison, in key order.
        show_same: Also print the keys whose values match (`--all`).

    Returns:
        The lines. Empty when nothing is to be printed.
    """
    lines: list[str] = []
    for entry in entries:
        if entry.status is Status.SAME and not show_same:
            continue
        texts = (entry.key, entry.value1, entry.value2)
        lines.append("  ".join([entry.status, *(json.dumps(t, ensure_ascii=False) for t in texts)]))
    return "\n".join(lines)


def _list(_args: argparse.Namespace) -> int:
    text = format_list(registry.TOOLS)
    if text:
        print(text)
    return 0


def _text_count(args: argparse.Namespace) -> int:
    text: str | None = args.text
    print(format_text_count(count_text(read_stdin() if text is None else text)))
    return 0


def _regex(args: argparse.Namespace) -> int:
    given: str | None = args.text
    match = partial(
        match_pattern,
        args.pattern,
        read_stdin() if given is None else given,
        all_matches=args.all_matches,
        ignore_case=args.ignore_case,
        multiline=args.multiline,
    )
    with Worker() as worker:
        output = format_matches(worker.call(match))
    if output:
        print(output)
    return 0


def _resx_compare(args: argparse.Namespace) -> int:
    output = format_resx(compare_resx(Path(args.file1), Path(args.file2)), show_same=args.all)
    if output:
        print(output)
    return 0


def format_finding(finding: Finding) -> str:
    """Render a finding as `pytoolbox hardcoding` prints it: `path:line  kind  "text"`."""
    return f"{finding.path.as_posix()}:{finding.line}  {finding.kind}  {_quoted(finding.text)}"


def _hardcoding(args: argparse.Namespace) -> int:
    scan = scan_hardcoding(
        Path(args.folder),
        extensions=args.ext or DEFAULT_EXTENSIONS,
        exclude=args.exclude or DEFAULT_EXCLUDE,
        kinds=args.kind or KIND_NAMES,
    )
    for value in scan:
        if isinstance(value, Finding):
            print(format_finding(value))
        elif isinstance(value, HardcodingResult):
            for failure in value.failures:
                print(f"skipped: {failure.path.as_posix()}: {failure.message}", file=sys.stderr)
    return 0


def format_sizes(result: FolderSizeResult, top: int | None = None) -> str:
    """Render a folder-size result as `pytoolbox folder-size` prints it.

    Args:
        result: The result.
        top: Print only the first `top` items, and a line that counts the rest.

    Returns:
        One `size  kind  name` line per item, then a blank line, the total, and the count of
        unreadable items when there are any.
    """
    shown: Sequence[SizeItem] = result.items[:top] if top else result.items
    sizes = [human_size(item.size) for item in shown]
    size_width = max(map(len, sizes), default=0)
    kind_width = max((len(item.kind) for item in shown), default=0)
    lines = [
        f"{size:>{size_width}}  {item.kind:<{kind_width}}  {item.name}"
        for size, item in zip(sizes, shown, strict=True)
    ]
    if hidden := len(result.items) - len(shown):
        lines.append(f"... {hidden} more")
    lines += ["", f"total: {human_size(result.total)} ({len(result.items)} items)"]
    if result.unreadable:
        lines.append(f"unreadable: {result.unreadable}")
    if result.cancelled:
        lines.append("cancelled")
    return "\n".join(lines).lstrip("\n")


def _folder_size(args: argparse.Namespace) -> int:
    top: int | None = args.top
    if top is not None and top < 1:
        msg = "--top must be 1 or more."
        raise InputError(msg)
    progress = sys.stderr.isatty()
    for value in folder_size(Path(args.folder)):
        if isinstance(value, FolderSizeResult):
            if progress:
                print("\r\033[K", end="", file=sys.stderr, flush=True)
            if args.csv:
                # Bytes, so Windows text mode does not turn the CSV's CRLF into CR CR LF.
                sys.stdout.flush()
                sys.stdout.buffer.write(to_csv(value.items).encode("utf-8"))
                sys.stdout.buffer.flush()
            else:
                print(format_sizes(value, top))
        elif progress:
            print(f"\rfolders {value.completed}/{value.total}", end="", file=sys.stderr, flush=True)
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
        0 on success, 1 on `InputError`, 2 on `OperationError`, 130 on Ctrl+C.
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
    regex = commands.add_parser(registry.REGEX.name, help=registry.REGEX.summary)
    regex.add_argument("pattern", help="The regular expression, in Python `re` syntax.")
    regex.add_argument("text", nargs="?", help="The text. Omit it to read standard input.")
    regex.add_argument(
        "-g", "--global", dest="all_matches", action="store_true", help="Show every match."
    )
    regex.add_argument("-i", "--ignore-case", action="store_true", help="Ignore case.")
    regex.add_argument(
        "-m", "--multiline", action="store_true", help="Let ^ and $ match at line breaks."
    )
    regex.set_defaults(handler=_regex)
    resx = commands.add_parser(registry.RESX_COMPARE.name, help=registry.RESX_COMPARE.summary)
    resx.add_argument("file1", help="The first .resx file.")
    resx.add_argument("file2", help="The second .resx file.")
    resx.add_argument("--all", action="store_true", help="Also show the keys that match.")
    resx.set_defaults(handler=_resx_compare)
    hardcoding = commands.add_parser(registry.HARDCODING.name, help=registry.HARDCODING.summary)
    hardcoding.add_argument("folder", help="The folder to scan.")
    hardcoding.add_argument(
        "--ext",
        action="append",
        help=f"File extensions, comma-separated. Default: {','.join(DEFAULT_EXTENSIONS)}.",
    )
    hardcoding.add_argument(
        "--exclude",
        action="append",
        help=f"Folder names to skip, comma-separated. Default: {','.join(DEFAULT_EXCLUDE)}.",
    )
    hardcoding.add_argument(
        "--kind",
        action="append",
        help=f"Kinds to find, comma-separated: {','.join(KIND_NAMES)}. Default: all.",
    )
    hardcoding.set_defaults(handler=_hardcoding)
    sizes = commands.add_parser(registry.FOLDER_SIZE.name, help=registry.FOLDER_SIZE.summary)
    sizes.add_argument("folder", nargs="?", default=".", help="The folder. Default: here.")
    sizes.add_argument("--top", type=int, metavar="N", help="Print only the largest N items.")
    sizes.add_argument(
        "--csv", action="store_true", help="Print every item as CSV (UTF-8 with a BOM) instead."
    )
    sizes.set_defaults(handler=_folder_size)
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
    except KeyboardInterrupt:
        return INTERRUPTED


def run() -> NoReturn:
    """Entry point of the `pytoolbox` script. Standard output and error are UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
