"""The GUI screen of each tool. Every tool in the registry has one."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from pytoolbox.registry import TEXT_COUNT, Tool
from pytoolbox.tools.dev.text import count_text
from pytoolbox_gui.sse import Stream

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
    """

    tool: Tool
    template: str
    icon: str
    run: Callable[[Form], Fragment] | None = None
    stream: Callable[[Form], Stream] | None = None


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


def run_text_count(form: Form) -> Fragment:
    """Count the `text` field for the `text-count` screen."""
    return Fragment("tools/text-count-result.html", {"count": count_text(text_field(form, "text"))})


SCREENS: tuple[Screen, ...] = (
    Screen(TEXT_COUNT, "tools/text-count.html", "text_word_count", run=run_text_count),
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
