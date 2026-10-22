"""Regular expression matching."""

import re
from dataclasses import dataclass

from pytoolbox.errors import InputError


@dataclass(frozen=True)
class Group:
    """One capture group of a match.

    Positions count code points from the start of the text; `end` is exclusive.

    Attributes:
        number: The group number, from 1.
        name: The group name, or `None` for a group without one.
        start: Where the group starts, or `None` when it took no part in the match.
        end: Where the group ends, or `None` when it took no part in the match.
        text: What the group captured, or `None` when it took no part in the match.
    """

    number: int
    name: str | None
    start: int | None
    end: int | None
    text: str | None


@dataclass(frozen=True)
class Match:
    """One match of a pattern.

    Positions count code points from the start of the text; `end` is exclusive.

    Attributes:
        start: Where the match starts.
        end: Where the match ends.
        text: The matched text.
        groups: Every capture group of the pattern, in number order.
    """

    start: int
    end: int
    text: str
    groups: tuple[Group, ...]


def match_pattern(
    pattern: str,
    text: str,
    *,
    all_matches: bool = False,
    ignore_case: bool = False,
    multiline: bool = False,
) -> tuple[Match, ...]:
    """Match a pattern against a text with Python's `re`.

    Args:
        pattern: The regular expression, in `re` syntax.
        text: The text to search.
        all_matches: Return every match (`g`). Without it, return the first match only.
        ignore_case: Match without regard to case (`i`, `re.IGNORECASE`).
        multiline: `^` and `$` also match at line breaks (`m`, `re.MULTILINE`).

    Returns:
        The matches, in text order. Empty when nothing matches.

    Raises:
        InputError: The pattern is not a valid regular expression.
    """
    flags = re.NOFLAG
    if ignore_case:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.MULTILINE
    try:
        compiled = re.compile(pattern, flags)
    except re.error as error:
        msg = f"Invalid pattern: {error}"
        raise InputError(msg) from error
    names = {number: name for name, number in compiled.groupindex.items()}
    if all_matches:
        found = list(compiled.finditer(text))
    else:
        first = compiled.search(text)
        found = [] if first is None else [first]
    return tuple(_match(match, names) for match in found)


def _match(match: re.Match[str], names: dict[int, str]) -> Match:
    groups: list[Group] = []
    for number in range(1, match.re.groups + 1):
        captured: str | None = match.group(number)
        start, end = match.span(number) if captured is not None else (None, None)
        groups.append(Group(number, names.get(number), start, end, captured))
    return Match(match.start(), match.end(), match.group(), tuple(groups))
