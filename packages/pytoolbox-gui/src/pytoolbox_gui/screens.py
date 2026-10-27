"""The GUI screen of each tool. Every tool in the registry has one."""

import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from pytoolbox.errors import InputError
from pytoolbox.registry import HARDCODING, REGEX, RESX_COMPARE, TEXT_COUNT, Tool
from pytoolbox.results import Cancel, Progress
from pytoolbox.tools.dev.hardcoding import (
    DEFAULT_EXCLUDE,
    DEFAULT_EXTENSIONS,
    KIND_NAMES,
    Finding,
    HardcodingResult,
    scan_hardcoding,
)
from pytoolbox.tools.dev.regex import Match, match_pattern
from pytoolbox.tools.dev.resx import Status, compare_resx
from pytoolbox.tools.dev.text import count_text
from pytoolbox.worker import Worker
from pytoolbox_gui.sse import Report, Stream, Value

MATCH_TIMEOUT = 1.0
"""Seconds the regex screen waits for one match (docs/design.ja.md §2)."""

_regex_worker = Worker()


def close_workers() -> None:
    """Stop the regex worker process, if one is running."""
    _regex_worker.close()


type Form = Mapping[str, Sequence[str]]
"""Form fields by name. A field can repeat, so each maps to its values."""


@dataclass(frozen=True)
class Fragment:
    """A template and its context, rendered as one HTML fragment.

    Attributes:
        template: The template name.
        context: The values the template reads.
    """

    template: str
    context: Mapping[str, object] = field(default_factory=dict[str, object])


@dataclass(frozen=True)
class Screen:
    """How the GUI shows and runs one tool.

    Attributes:
        tool: The registry entry.
        template: The pane template, shown at `GET /tools/<tool>`.
        icon: The Fluent icon name, between `ic_fluent_` and `_20_regular` in its file name
            under `static/icons/`.
        run: For a tool that returns at once: takes the form, returns the result fragment.
        stream: For a streaming tool: takes the form, returns the stream to run.
        context: Values the pane template reads besides `tool`, such as field defaults.
    """

    tool: Tool
    template: str
    icon: str
    run: Callable[[Form], Fragment] | None = None
    stream: Callable[[Form], Stream] | None = None
    context: Mapping[str, object] = field(default_factory=dict[str, object])


def text_field(form: Form, name: str) -> str:
    """Return a text field as the user typed it.

    A browser submits a textarea's line breaks as CRLF. They become LF again, so a tool sees
    the text the box shows.

    Args:
        form: The submitted form.
        name: The field name. A missing field is empty text; a repeated field gives its last value.

    Returns:
        The text.
    """
    values = form.get(name, ())
    return values[-1].replace("\r\n", "\n") if values else ""


def checkbox(form: Form, name: str) -> bool:
    """Say whether a checkbox is checked. A browser submits a checkbox only when it is."""
    return name in form


def run_text_count(form: Form) -> Fragment:
    """Count the `text` field for the `text-count` screen."""
    return Fragment("tools/text-count-result.html", {"count": count_text(text_field(form, "text"))})


@dataclass(frozen=True)
class Segment:
    """A run of the text on the regex screen.

    Attributes:
        text: The characters.
        match: The number, from 1, of the match the run is, or `None` outside every match.
    """

    text: str
    match: int | None


def highlight(text: str, matches: Sequence[Match]) -> tuple[Segment, ...]:
    """Split a text into the runs inside and outside its matches.

    Args:
        text: The text that was searched.
        matches: Its matches, in text order and not overlapping, as `re` returns them.

    Returns:
        The runs, in order. An empty match is an empty run in its place.
    """
    segments: list[Segment] = []
    position = 0
    for number, match in enumerate(matches, start=1):
        if match.start > position:
            segments.append(Segment(text[position : match.start], None))
        segments.append(Segment(match.text, number))
        position = match.end
    if position < len(text):
        segments.append(Segment(text[position:], None))
    return tuple(segments)


def run_regex(form: Form) -> Fragment:
    """Match the `pattern` field against the `text` field for the `regex` screen.

    The match runs in a worker process and stops after `MATCH_TIMEOUT` seconds.

    Raises:
        InputError: The pattern is not valid.
        OperationError: The match timed out.
    """
    text = text_field(form, "text")
    match = partial(
        match_pattern,
        text_field(form, "pattern"),
        text,
        all_matches=checkbox(form, "global"),
        ignore_case=checkbox(form, "ignore_case"),
        multiline=checkbox(form, "multiline"),
    )
    matches = _regex_worker.call(match, MATCH_TIMEOUT)
    return Fragment(
        "tools/regex-result.html", {"matches": matches, "segments": highlight(text, matches)}
    )


def path_field(form: Form, name: str, label: str) -> Path | None:
    """Return a path field as an absolute path.

    Surrounding spaces and one pair of surrounding double quotes are dropped; Explorer's
    "Copy as path" adds the quotes.

    Args:
        form: The submitted form.
        name: The field name.
        label: How the screen names the field, for the error message.

    Returns:
        The path, or `None` when the field is empty.

    Raises:
        InputError: The path is relative. The server's working directory is not visible on
            the screen.
    """
    text = text_field(form, name).strip()
    if len(text) > 1 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        msg = f"{label}: use an absolute path, not {text}."
        raise InputError(msg)
    return path


def run_resx_compare(form: Form) -> Fragment:
    """Compare the `file1` and `file2` fields for the `resx-compare` screen.

    The fragment counts every status and lists the keys that differ. While a path is empty,
    nothing is compared and the fragment asks for both paths.

    Raises:
        InputError: A path is relative, or a file is missing or broken.
        OperationError: A file exists but cannot be read.
    """
    path1 = path_field(form, "file1", "File 1")
    path2 = path_field(form, "file2", "File 2")
    if path1 is None or path2 is None:
        return Fragment("tools/resx-compare-result.html", {"counts": None, "differences": ()})
    entries = compare_resx(path1, path2)
    counts = dict.fromkeys(Status, 0)
    for entry in entries:
        counts[entry.status] += 1
    differences = [entry for entry in entries if entry.status is not Status.SAME]
    return Fragment(
        "tools/resx-compare-result.html", {"counts": counts, "differences": differences}
    )


SHOWN_FINDINGS = 1000
"""Findings the hardcoding screen lists. Every finding is counted (docs/design.ja.md §5)."""


class _LatestScan:
    """Keeps one hardcoding scan running per server: a new scan stops the one before."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def supersede(self) -> Cancel:
        """Stop the scan started before, and return the stop check of the next one."""
        with self._lock:
            self._stop.set()
            self._stop = threading.Event()
            return self._stop.is_set


_latest_scan = _LatestScan()


def stream_hardcoding(form: Form) -> Stream:
    """Start a scan from the `hardcoding` form.

    The fields are `folder`, `extensions`, `exclude`, and one `kind` per checked kind. They
    are checked before the run starts. Starting a scan stops the one started before it.

    Raises:
        InputError: The folder is empty, relative, or missing, no extension or kind is given.
    """
    folder = path_field(form, "folder", "Folder")
    if folder is None:
        msg = "Folder: enter a path."
        raise InputError(msg)
    stops: list[Cancel] = []

    def cancelled() -> bool:
        return any(stop() for stop in stops)

    values = scan_hardcoding(
        folder,
        extensions=[text_field(form, "extensions")],
        exclude=[text_field(form, "exclude")],
        kinds=form.get("kind", ()),
        cancel=cancelled,
    )
    stops.append(_latest_scan.supersede())

    def stream(cancel: Cancel) -> Iterator[Value]:
        stops.append(cancel)
        shown: list[Finding] = []
        for value in values:
            if isinstance(value, Finding):
                if len(shown) < SHOWN_FINDINGS:
                    shown.append(value)
            elif isinstance(value, Progress):
                yield value
            elif isinstance(value, HardcodingResult):
                yield Report("tools/hardcoding-result.html", {"result": value, "findings": shown})

    return stream


SCREENS: tuple[Screen, ...] = (
    Screen(REGEX, "tools/regex.html", "text_asterisk", run=run_regex),
    Screen(TEXT_COUNT, "tools/text-count.html", "text_word_count", run=run_text_count),
    Screen(RESX_COMPARE, "tools/resx-compare.html", "branch_compare", run=run_resx_compare),
    Screen(
        HARDCODING,
        "tools/hardcoding.html",
        "document_search",
        stream=stream_hardcoding,
        context={
            "extensions": ", ".join(DEFAULT_EXTENSIONS),
            "exclude": ", ".join(DEFAULT_EXCLUDE),
            "kinds": KIND_NAMES,
        },
    ),
)
"""Every screen. A tool row adds its screen here with its registry entry."""


def matches(tool: Tool, query: str) -> bool:
    """Say whether a search query finds a tool.

    Args:
        tool: The tool.
        query: The text typed into the search box. Empty text finds every tool.

    Returns:
        Whether the query is in the tool's name or summary, ignoring case.
    """
    needle = query.strip().casefold()
    return needle in tool.name.casefold() or needle in tool.summary.casefold()
