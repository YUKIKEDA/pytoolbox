"""The list of tools that the CLI and the GUI expose."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pytoolbox.errors import InputError


class System(StrEnum):
    """The two systems of the design, in the order the sidebar shows them."""

    DEVELOPER = "Developer Utilities"
    OPERATIONS = "System Operations"


@dataclass(frozen=True)
class Tool:
    """One tool: its CLI name and where the sidebar puts it.

    Attributes:
        name: The CLI command and the GUI path segment, in kebab-case.
        system: The system the tool belongs to.
        category: The sidebar category inside the system.
        summary: One English line for `pytoolbox list` and the sidebar.
    """

    name: str
    system: System
    category: str
    summary: str


REGEX = Tool(
    "regex",
    System.DEVELOPER,
    "Text",
    "Match a regular expression against a text and show each match and its groups.",
)

TEXT_COUNT = Tool(
    "text-count",
    System.DEVELOPER,
    "Text",
    "Count characters, characters that are not white space, and half-width characters.",
)

RESX_COMPARE = Tool(
    "resx-compare",
    System.DEVELOPER,
    "Code",
    "Compare the data names and values of two .resx files.",
)

HARDCODING = Tool(
    "hardcoding",
    System.DEVELOPER,
    "Code",
    "Find hard-coded strings, numbers, IPs, URLs, and email addresses in a folder's sources.",
)

FOLDER_SIZE = Tool(
    "folder-size",
    System.OPERATIONS,
    "Storage",
    "List a folder's direct files and folders, largest first, with each folder's total size.",
)

TOOLS: tuple[Tool, ...] = (REGEX, TEXT_COUNT, RESX_COMPARE, HARDCODING, FOLDER_SIZE)
"""Every tool, in sidebar order. A tool row adds its entry here."""


def check_unique(tools: Iterable[Tool]) -> None:
    """Refuse a tool list in which two tools share a name.

    Args:
        tools: The tools to check.

    Raises:
        ValueError: Two tools share a name.
    """
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            msg = f"Duplicate tool name: {tool.name}"
            raise ValueError(msg)
        seen.add(tool.name)


def find(tools: Iterable[Tool], name: str) -> Tool:
    """Return the tool with the given name.

    Args:
        tools: The tools to search.
        name: The CLI name.

    Returns:
        The tool.

    Raises:
        InputError: No tool has that name.
    """
    for tool in tools:
        if tool.name == name:
            return tool
    msg = f"Unknown tool: {name}"
    raise InputError(msg)


def group(tools: Sequence[Tool]) -> dict[System, dict[str, list[Tool]]]:
    """Group tools by system, then by category.

    Systems follow the order of `System`. Categories and tools keep the order of `tools`.
    Systems and categories without tools are left out.

    Args:
        tools: The tools to group.

    Returns:
        A mapping from system to category to tools.
    """
    by_system: dict[System, dict[str, list[Tool]]] = {}
    for system in System:
        categories: dict[str, list[Tool]] = {}
        for tool in tools:
            if tool.system is system:
                categories.setdefault(tool.category, []).append(tool)
        if categories:
            by_system[system] = categories
    return by_system
