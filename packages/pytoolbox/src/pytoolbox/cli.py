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
from pytoolbox.results import LogEvent, clock
from pytoolbox.tools.dev.csv_gen import generate_csv, parse_columns
from pytoolbox.tools.dev.hardcoding import (
    DEFAULT_EXCLUDE,
    DEFAULT_EXTENSIONS,
    KIND_NAMES,
    Finding,
    HardcodingResult,
    scan_hardcoding,
)
from pytoolbox.tools.dev.json_gen import generate_json, load_schema_input
from pytoolbox.tools.dev.mock_image import (
    IMAGE_FORMATS,
    MockImageResult,
    generate_mock_images,
)
from pytoolbox.tools.dev.regex import Group, Match, match_pattern
from pytoolbox.tools.dev.resx import Entry, Status, compare_resx
from pytoolbox.tools.dev.text import TextCount, count_text
from pytoolbox.tools.system import network
from pytoolbox.tools.system.cleanup import CACHE_TARGETS, CacheCleanResult, clean_caches
from pytoolbox.tools.system.file_copy import DEFAULT_FORMAT as FILE_COPY_DEFAULT_FORMAT
from pytoolbox.tools.system.file_copy import FileCopyResult, copy_file_batch
from pytoolbox.tools.system.folder_copy import DEFAULT_FORMAT as FOLDER_COPY_DEFAULT_FORMAT
from pytoolbox.tools.system.folder_copy import FolderCopyResult, copy_folder_batch
from pytoolbox.tools.system.folder_size import (
    FolderSizeResult,
    SizeItem,
    folder_size,
    human_size,
    to_csv,
)
from pytoolbox.tools.system.image_clip import (
    ImageClipResult,
    clip_images,
    parse_box,
)
from pytoolbox.tools.system.network import DEFAULT_TIMEOUT as NETWORK_DEFAULT_TIMEOUT
from pytoolbox.tools.system.network import PingResult, PortResult
from pytoolbox.tools.system.pdf_split import (
    DEFAULT_FORMAT as PDF_SPLIT_DEFAULT_FORMAT,
)
from pytoolbox.tools.system.pdf_split import PdfSplitResult, split_pdf
from pytoolbox.tools.system.udp import DEFAULT_PORT as UDP_DEFAULT_PORT
from pytoolbox.tools.system.udp import HOST as UDP_HOST
from pytoolbox.tools.system.udp import listen_udp
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


def format_file_copy(result: FileCopyResult) -> str:
    """Render a file-copy result as `pytoolbox file-copy` prints it.

    Args:
        result: The file-copy outcome.

    Returns:
        One line per planned or copied path, a blank line when non-empty, and the summary.
    """
    lines = [str(path) for path in result.items]
    if lines:
        lines.append("")
    mode = "dry-run" if result.dry_run else "copied"
    verb = "planned" if result.dry_run else "files"
    summary = f"{mode}: {result.batch.succeeded} {verb}"
    if result.batch.failures:
        summary += f", {len(result.batch.failures)} failed"
    if result.batch.cancelled:
        summary += ", cancelled"
    lines.append(summary)
    return "\n".join(lines)


def _file_copy(args: argparse.Namespace) -> int:
    if not str(args.source).strip():
        msg = "Source path must not be empty."
        raise InputError(msg)
    if not str(args.dest_dir).strip():
        msg = "Destination folder must not be empty."
        raise InputError(msg)
    progress = sys.stderr.isatty()
    stream = copy_file_batch(
        Path(args.source),
        Path(args.dest_dir),
        args.count,
        fmt=args.format,
        start=args.start,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    for value in stream:
        if isinstance(value, FileCopyResult):
            if progress:
                print("\r\033[K", end="", file=sys.stderr, flush=True)
            print(format_file_copy(value))
            for failure in value.batch.failures:
                print(f"failed: {failure.path}: {failure.message}", file=sys.stderr)
        elif progress:
            print(
                f"\rfile-copy {value.completed}/{value.total}",
                end="",
                file=sys.stderr,
                flush=True,
            )
    return 0


def format_folder_copy(result: FolderCopyResult) -> str:
    """Render a folder-copy result as `pytoolbox folder-copy` prints it.

    Args:
        result: The folder-copy outcome.

    Returns:
        One line per planned or copied folder, a blank line when non-empty, and the summary.
    """
    lines = [str(path) for path in result.items]
    if lines:
        lines.append("")
    mode = "dry-run" if result.dry_run else "applied"
    verb = "planned" if result.dry_run else "copied"
    summary = f"{mode}: {result.batch.succeeded} {verb}"
    if result.batch.failures:
        summary += f", {len(result.batch.failures)} failed"
    if result.batch.cancelled:
        summary += ", cancelled"
    lines.append(summary)
    return "\n".join(lines)


def _folder_copy(args: argparse.Namespace) -> int:
    progress = sys.stderr.isatty()
    for value in copy_folder_batch(
        Path(args.source),
        Path(args.dest_dir),
        args.count,
        fmt=args.format,
        start=args.start,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    ):
        if isinstance(value, FolderCopyResult):
            if progress:
                print("\r\033[K", end="", file=sys.stderr, flush=True)
            print(format_folder_copy(value))
            for failure in value.batch.failures:
                print(f"failed: {failure.path}: {failure.message}", file=sys.stderr)
        elif progress:
            print(
                f"\rfolder-copy {value.completed}/{value.total}",
                end="",
                file=sys.stderr,
                flush=True,
            )
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


def format_cache_clean(result: CacheCleanResult) -> str:
    """Render a cache-clean result as `pytoolbox cache-clean` prints it.

    Args:
        result: The cache-clean outcome.

    Returns:
        One line per planned or deleted file, a blank line when non-empty, and the summary.
    """
    lines: list[str] = []
    if result.items:
        target_width = max(len(item.target) for item in result.items)
        sizes = [human_size(item.bytes) for item in result.items]
        size_width = max(len(size) for size in sizes)
        lines.extend(
            f"{item.target:<{target_width}}  {size:>{size_width}}  {item.path}"
            for item, size in zip(result.items, sizes, strict=True)
        )
        lines.append("")
    mode = "dry-run" if result.dry_run else "applied"
    verb = "planned" if result.dry_run else "deleted"
    summary = (
        f"{mode}: {result.batch.succeeded} {verb}, "
        f"{human_size(result.total_bytes)} ({result.total_bytes} bytes)"
    )
    if result.batch.failures:
        summary += f", {len(result.batch.failures)} failed"
    if result.batch.cancelled:
        summary += ", cancelled"
    lines.append(summary)
    return "\n".join(lines)


def _cache_clean(args: argparse.Namespace) -> int:
    progress = sys.stderr.isatty()
    for value in clean_caches(args.targets, apply=args.apply):
        if isinstance(value, CacheCleanResult):
            if progress:
                print("\r\033[K", end="", file=sys.stderr, flush=True)
            print(format_cache_clean(value))
            for failure in value.batch.failures:
                print(f"failed: {failure.path}: {failure.message}", file=sys.stderr)
        elif progress:
            print(
                f"\rcache-clean {value.completed}/{value.total}",
                end="",
                file=sys.stderr,
                flush=True,
            )
    return 0


def format_ping(result: PingResult) -> str:
    """Render a ping result as `pytoolbox ping` prints it."""
    if result.reachable:
        latency = f" ({result.latency_ms:.1f} ms)" if result.latency_ms is not None else ""
        summary = f"{result.host}: reachable{latency}"
    else:
        summary = f"{result.host}: unreachable"
    return f"{summary}\n{result.output}" if result.output else summary


def _ping(args: argparse.Namespace) -> int:
    print(format_ping(network.ping_host(args.host, timeout=args.timeout)))
    return 0


def format_port(result: PortResult) -> str:
    """Render a port check result as `pytoolbox port` prints it."""
    target = f"{result.host}:{result.port}"
    if result.is_open:
        if result.latency_ms is None:
            return f"{target}  open"
        return f"{target}  open  {result.latency_ms:.1f} ms"
    return f"{target}  closed  {result.detail}"


def _port(args: argparse.Namespace) -> int:
    print(format_port(network.check_port(args.host, args.port, timeout=args.timeout)))
    return 0


def _udp(args: argparse.Namespace) -> int:
    port: int = args.port
    session = listen_udp(port)
    print(f"listening on {UDP_HOST}:{port} (Ctrl+C to stop)", file=sys.stderr, flush=True)
    for value in session:
        if isinstance(value, LogEvent):
            print(f"{clock(value.time)}  {value.message}", flush=True)
    return 0


def _csv_gen(args: argparse.Namespace) -> int:
    columns = parse_columns(args.col)
    result = generate_csv(
        Path(args.output),
        args.rows,
        columns,
        seed=args.seed,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    verb = "dry-run" if result.dry_run else "wrote"
    print(f"{verb}: {result.output} ({result.rows} rows)")
    return 0


def _json_gen(args: argparse.Namespace) -> int:
    schema = load_schema_input(args.schema, args.col)
    result = generate_json(
        Path(args.output),
        args.rows,
        schema,
        seed=args.seed,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    verb = "dry-run" if result.dry_run else "wrote"
    print(f"{verb}: {result.output} ({result.rows} rows)")
    return 0


def format_mock_image(result: MockImageResult) -> str:
    """Render a mock-image result as `pytoolbox mock-image` prints it.

    Args:
        result: The mock-image outcome.

    Returns:
        One line per planned or written image, a blank line when non-empty, and the summary.
    """
    lines: list[str] = []
    if result.items:
        dims = [f"{item.width}x{item.height}" for item in result.items]
        dim_width = max(len(dim) for dim in dims)
        if result.dry_run:
            lines.extend(
                f"{dim:<{dim_width}}  {item.path}"
                for item, dim in zip(result.items, dims, strict=True)
            )
        else:
            sizes = [human_size(item.bytes) for item in result.items]
            size_width = max(len(size) for size in sizes)
            lines.extend(
                f"{dim:<{dim_width}}  {size:>{size_width}}  {item.path}"
                for item, dim, size in zip(result.items, dims, sizes, strict=True)
            )
        lines.append("")
    total_bytes = sum(item.bytes for item in result.items)
    if result.dry_run:
        summary = f"dry-run: {result.batch.succeeded} planned ({result.format})"
    else:
        summary = (
            f"wrote: {result.batch.succeeded} images, "
            f"{human_size(total_bytes)} ({total_bytes} bytes)"
        )
    if result.batch.failures:
        summary += f", {len(result.batch.failures)} failed"
    if result.batch.cancelled:
        summary += ", cancelled"
    lines.append(summary)
    return "\n".join(lines)


def _mock_image(args: argparse.Namespace) -> int:
    progress = sys.stderr.isatty()
    for value in generate_mock_images(
        Path(args.output_dir),
        args.width,
        args.height,
        args.count,
        random_ratio=args.random_ratio,
        image_format=args.format,
        seed=args.seed,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    ):
        if isinstance(value, MockImageResult):
            if progress:
                print("\r\033[K", end="", file=sys.stderr, flush=True)
            print(format_mock_image(value))
            for failure in value.batch.failures:
                print(f"failed: {failure.path}: {failure.message}", file=sys.stderr)
        elif progress:
            print(
                f"\rmock-image {value.completed}/{value.total}",
                end="",
                file=sys.stderr,
                flush=True,
            )
    return 0


def format_pdf_split(result: PdfSplitResult) -> str:
    """Render a pdf-split result as `pytoolbox pdf-split` prints it.

    Args:
        result: The pdf-split outcome.

    Returns:
        One line per planned or written page file, a blank line when non-empty,
        and the summary.
    """
    lines = [str(path) for path in result.items]
    if lines:
        lines.append("")
    if result.dry_run:
        summary = f"dry-run: {result.batch.succeeded} pages planned"
    else:
        summary = f"wrote: {result.batch.succeeded} pages"
    if result.batch.failures:
        summary += f", {len(result.batch.failures)} failed"
    if result.batch.cancelled:
        summary += ", cancelled"
    lines.append(summary)
    return "\n".join(lines)


def _pdf_split(args: argparse.Namespace) -> int:
    progress = sys.stderr.isatty()
    for value in split_pdf(
        Path(args.input),
        Path(args.output_dir),
        fmt=args.format,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    ):
        if isinstance(value, PdfSplitResult):
            if progress:
                print("\r\033[K", end="", file=sys.stderr, flush=True)
            print(format_pdf_split(value))
            for failure in value.batch.failures:
                print(f"failed: {failure.path}: {failure.message}", file=sys.stderr)
        elif progress:
            print(
                f"\rpdf-split {value.completed}/{value.total}",
                end="",
                file=sys.stderr,
                flush=True,
            )
    return 0


def format_image_clip(result: ImageClipResult) -> str:
    """Render an image-clip result as `pytoolbox image-clip` prints it.

    Args:
        result: The image-clip outcome.

    Returns:
        One line per planned or cropped image, a blank line when non-empty, and the summary.
    """
    lines: list[str] = []
    if result.items:
        boxes = [
            f"{item.box.x1},{item.box.y1},{item.box.x2},{item.box.y2}" for item in result.items
        ]
        box_width = max(len(b) for b in boxes)
        lines.extend(
            f"{b:<{box_width}}  {item.output}" for item, b in zip(result.items, boxes, strict=True)
        )
        lines.append("")
    if result.dry_run:
        summary = f"dry-run: {result.batch.succeeded} planned"
    else:
        summary = f"wrote: {result.batch.succeeded} cropped"
    if result.batch.failures:
        summary += f", {len(result.batch.failures)} failed"
    if result.batch.cancelled:
        summary += ", cancelled"
    lines.append(summary)
    return "\n".join(lines)


def _image_clip(args: argparse.Namespace) -> int:
    box = parse_box(args.box)
    output_dir = Path(args.output_dir) if args.output_dir is not None else None
    progress = sys.stderr.isatty()
    for value in clip_images(
        Path(args.input_dir),
        box,
        output_dir=output_dir,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    ):
        if isinstance(value, ImageClipResult):
            if progress:
                print("\r\033[K", end="", file=sys.stderr, flush=True)
            print(format_image_clip(value))
            for failure in value.batch.failures:
                print(f"failed: {failure.path}: {failure.message}", file=sys.stderr)
        elif progress:
            print(
                f"\rimage-clip {value.completed}/{value.total}",
                end="",
                file=sys.stderr,
                flush=True,
            )
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
    file_copy = commands.add_parser(registry.FILE_COPY.name, help=registry.FILE_COPY.summary)
    file_copy.add_argument("source", metavar="SOURCE", help="Source file path.")
    file_copy.add_argument("dest_dir", metavar="DEST_DIR", help="Destination folder path.")
    file_copy.add_argument(
        "--count", type=int, required=True, metavar="N", help="Number of copies."
    )
    file_copy.add_argument(
        "--format",
        default=FILE_COPY_DEFAULT_FORMAT,
        metavar="FMT",
        help=f"Output name template. Default: {FILE_COPY_DEFAULT_FORMAT}.",
    )
    file_copy.add_argument(
        "--start",
        type=int,
        default=1,
        metavar="N",
        help="Starting index for {n}. Default: 1.",
    )
    file_copy.add_argument("--overwrite", action="store_true", help="Replace existing files.")
    file_copy.add_argument(
        "--dry-run", action="store_true", help="Report planned paths without copying."
    )
    file_copy.set_defaults(handler=_file_copy)
    folder_copy = commands.add_parser(registry.FOLDER_COPY.name, help=registry.FOLDER_COPY.summary)
    folder_copy.add_argument("source", help="Source folder path.")
    folder_copy.add_argument("dest_dir", help="Destination directory path.")
    folder_copy.add_argument(
        "--count", type=int, required=True, metavar="N", help="Number of copies to create."
    )
    folder_copy.add_argument(
        "--format",
        default=FOLDER_COPY_DEFAULT_FORMAT,
        metavar="FMT",
        help=f"Target folder name format. Default: {FOLDER_COPY_DEFAULT_FORMAT}.",
    )
    folder_copy.add_argument(
        "--start", type=int, default=1, metavar="N", help="Starting number. Default: 1."
    )
    folder_copy.add_argument(
        "--overwrite", action="store_true", help="Replace existing target folders."
    )
    folder_copy.add_argument(
        "--dry-run", action="store_true", help="Report planned target paths without copying."
    )
    folder_copy.set_defaults(handler=_folder_copy)
    sizes = commands.add_parser(registry.FOLDER_SIZE.name, help=registry.FOLDER_SIZE.summary)
    sizes.add_argument("folder", nargs="?", default=".", help="The folder. Default: here.")
    sizes.add_argument("--top", type=int, metavar="N", help="Print only the largest N items.")
    sizes.add_argument(
        "--csv", action="store_true", help="Print every item as CSV (UTF-8 with a BOM) instead."
    )
    sizes.set_defaults(handler=_folder_size)
    cleanup = commands.add_parser(registry.CACHE_CLEAN.name, help=registry.CACHE_CLEAN.summary)
    cleanup.add_argument(
        "targets",
        nargs="+",
        metavar="TARGET",
        help=f"Cache targets to clean: {', '.join(CACHE_TARGETS)}.",
    )
    cleanup_mode = cleanup.add_mutually_exclusive_group()
    cleanup_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned paths and byte counts without deleting (default).",
    )
    cleanup_mode.add_argument(
        "--apply",
        action="store_true",
        help="Delete eligible cache contents and record the run in history.",
    )
    cleanup.set_defaults(handler=_cache_clean)
    port = commands.add_parser(registry.PORT.name, help=registry.PORT.summary)
    port.add_argument("host", help="The host name or IP address.")
    port.add_argument("port", type=int, help="The TCP port number, from 1 to 65535.")
    port.add_argument(
        "--timeout",
        type=float,
        default=NETWORK_DEFAULT_TIMEOUT,
        metavar="SEC",
        help=f"Timeout in seconds. Default: {NETWORK_DEFAULT_TIMEOUT:g}.",
    )
    port.set_defaults(handler=_port)
    ping = commands.add_parser(registry.PING.name, help=registry.PING.summary)
    ping.add_argument("host", help="The host name or IP address.")
    ping.add_argument(
        "--timeout",
        type=float,
        default=NETWORK_DEFAULT_TIMEOUT,
        metavar="SEC",
        help=f"Timeout in seconds. Default: {NETWORK_DEFAULT_TIMEOUT:g}.",
    )
    ping.set_defaults(handler=_ping)
    udp = commands.add_parser(registry.UDP.name, help=registry.UDP.summary)
    udp.add_argument(
        "--port", type=int, default=UDP_DEFAULT_PORT, help=f"Default: {UDP_DEFAULT_PORT}."
    )
    udp.set_defaults(handler=_udp)
    csv_gen = commands.add_parser(registry.CSV_GEN.name, help=registry.CSV_GEN.summary)
    csv_gen.add_argument("output", help="Destination CSV file path.")
    csv_gen.add_argument("--rows", type=int, required=True, metavar="N", help="Row count.")
    csv_gen.add_argument(
        "--col",
        action="append",
        required=True,
        metavar="NAME:TYPE",
        help="Column spec (`text`, `number`, `boolean`, `date`). Repeatable.",
    )
    csv_gen.add_argument("--seed", type=int, help="Optional random seed.")
    csv_gen.add_argument("--overwrite", action="store_true", help="Replace an existing file.")
    csv_gen.add_argument(
        "--dry-run", action="store_true", help="Report the target path without writing."
    )
    csv_gen.set_defaults(handler=_csv_gen)
    json_gen = commands.add_parser(registry.JSON_GEN.name, help=registry.JSON_GEN.summary)
    json_gen.add_argument("output", help="Destination JSON file path.")
    json_gen.add_argument("--rows", type=int, required=True, metavar="N", help="Object count.")
    json_gen.add_argument(
        "--schema", metavar="PATH_OR_JSON", help="JSON schema file or inline JSON."
    )
    json_gen.add_argument(
        "--col",
        action="append",
        metavar="NAME:TYPE",
        help="Simple property spec (`string`, `number`, etc.). Repeatable.",
    )
    json_gen.add_argument("--seed", type=int, help="Optional random seed.")
    json_gen.add_argument("--overwrite", action="store_true", help="Replace an existing file.")
    json_gen.add_argument(
        "--dry-run", action="store_true", help="Report the target path without writing."
    )
    json_gen.set_defaults(handler=_json_gen)
    mock_image = commands.add_parser(registry.MOCK_IMAGE.name, help=registry.MOCK_IMAGE.summary)
    mock_image.add_argument("output_dir", help="Destination directory for mock images.")
    mock_image.add_argument("--width", type=int, required=True, metavar="W", help="Width in px.")
    mock_image.add_argument("--height", type=int, required=True, metavar="H", help="Height in px.")
    mock_image.add_argument("--count", type=int, required=True, metavar="N", help="Image count.")
    mock_image.add_argument(
        "--random-ratio",
        action="store_true",
        help="Randomize dimensions up to the requested width and height.",
    )
    mock_image.add_argument(
        "--format",
        default="png",
        help=f"Output image format ({', '.join(IMAGE_FORMATS)}). Default: png.",
    )
    mock_image.add_argument("--seed", type=int, help="Optional random seed.")
    mock_image.add_argument("--overwrite", action="store_true", help="Replace existing files.")
    mock_image.add_argument(
        "--dry-run", action="store_true", help="Report planned paths without writing."
    )
    mock_image.set_defaults(handler=_mock_image)
    pdf_split = commands.add_parser(registry.PDF_SPLIT.name, help=registry.PDF_SPLIT.summary)
    pdf_split.add_argument("input", help="Source PDF file path.")
    pdf_split.add_argument("output_dir", help="Destination directory for split pages.")
    pdf_split.add_argument(
        "--format",
        default=PDF_SPLIT_DEFAULT_FORMAT,
        metavar="FMT",
        help=f"Name format ({{n}}, {{stem}}, {{name}}). Default: {PDF_SPLIT_DEFAULT_FORMAT}.",
    )
    pdf_split.add_argument("--overwrite", action="store_true", help="Replace existing files.")
    pdf_split.add_argument(
        "--dry-run", action="store_true", help="Report planned paths without writing."
    )
    pdf_split.set_defaults(handler=_pdf_split)
    image_clip = commands.add_parser(registry.IMAGE_CLIP.name, help=registry.IMAGE_CLIP.summary)
    image_clip.add_argument("input_dir", help="Folder containing source images to crop.")
    image_clip.add_argument(
        "--box",
        required=True,
        metavar="X1,Y1,X2,Y2",
        help="Pixel crop rectangle X1,Y1,X2,Y2.",
    )
    image_clip.add_argument(
        "--output-dir",
        metavar="DIR",
        help="Destination folder. Default: input folder.",
    )
    image_clip.add_argument("--overwrite", action="store_true", help="Replace existing files.")
    image_clip.add_argument(
        "--dry-run", action="store_true", help="Report planned paths without writing."
    )
    image_clip.set_defaults(handler=_image_clip)
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
