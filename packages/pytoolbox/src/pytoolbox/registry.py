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


CSV_GEN = Tool(
    "csv-gen",
    System.DEVELOPER,
    "Generators",
    "Generate a UTF-8 BOM CSV file from column types and a row count.",
)

JSON_GEN = Tool(
    "json-gen",
    System.DEVELOPER,
    "Generators",
    "Generate a UTF-8 JSON array file from property definitions and a row count.",
)

MOCK_IMAGE = Tool(
    "mock-image",
    System.DEVELOPER,
    "Generators",
    "Generate numbered PNG or JPEG mock images with pixel dimensions.",
)

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

FILE_MERGE = Tool(
    "file-merge",
    System.OPERATIONS,
    "Files",
    "Merge text files with a separator into a timestamped UTF-8 file.",
)

FOLDER_SIZE = Tool(
    "folder-size",
    System.OPERATIONS,
    "Storage",
    "List a folder's direct files and folders, largest first, with each folder's total size.",
)

CACHE_CLEAN = Tool(
    "cache-clean",
    System.OPERATIONS,
    "Cleanup",
    "Inspect or delete selected user caches (temp, pip, uv, npm, nuget, cargo).",
)

PORT = Tool(
    "port",
    System.OPERATIONS,
    "Network",
    "Check whether a host's TCP port is open and measure its response time.",
)

PING = Tool(
    "ping",
    System.OPERATIONS,
    "Network",
    "Send one ICMP echo to a host and report reachability and round-trip time.",
)

UDP = Tool(
    "udp",
    System.OPERATIONS,
    "Network",
    "Listen for UDP datagrams on 0.0.0.0 and log each one's length, hex, and text.",
)

TOOLS: tuple[Tool, ...] = (
    CSV_GEN,
    JSON_GEN,
    MOCK_IMAGE,
    REGEX,
    TEXT_COUNT,
    RESX_COMPARE,
    HARDCODING,
    FILE_MERGE,
    FOLDER_SIZE,
    CACHE_CLEAN,
    PORT,
    PING,
    UDP,
)
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
