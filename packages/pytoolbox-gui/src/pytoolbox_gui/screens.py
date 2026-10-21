"""The GUI screen of each tool. Every tool in the registry has one."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from pytoolbox.registry import Tool
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
        run: For a tool that returns at once: takes the form, returns the result fragment.
        stream: For a streaming tool: takes the form, returns the stream to run.
    """

    tool: Tool
    template: str
    run: Callable[[Form], Fragment] | None = None
    stream: Callable[[Form], Stream] | None = None


SCREENS: tuple[Screen, ...] = ()
"""Every screen. A tool row adds its screen here with its registry entry."""
