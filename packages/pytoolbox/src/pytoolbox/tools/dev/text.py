"""Text tools."""

import unicodedata
from dataclasses import dataclass

_HALF_WIDTH = frozenset({"H", "Na"})


@dataclass(frozen=True)
class TextCount:
    """The counts of one text.

    Attributes:
        total: Code points.
        non_space: Code points for which `str.isspace` is false.
        half_width: Code points whose East Asian Width is H or Na.
    """

    total: int
    non_space: int
    half_width: int


def count_text(text: str) -> TextCount:
    """Count the characters of a text.

    Args:
        text: The text, counted as given. Line breaks and spaces are characters.

    Returns:
        The total, the characters that are not white space, and the half-width characters.
    """
    return TextCount(
        total=len(text),
        non_space=sum(not char.isspace() for char in text),
        half_width=sum(unicodedata.east_asian_width(char) in _HALF_WIDTH for char in text),
    )
