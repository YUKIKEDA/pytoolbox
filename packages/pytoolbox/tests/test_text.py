import pytest

from pytoolbox.tools.dev.text import TextCount, count_text


def test_empty_text_counts_zero() -> None:
    assert count_text("") == TextCount(0, 0, 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abc", TextCount(3, 3, 3)),
        ("a b\tc\n", TextCount(6, 3, 4)),
        ("\u3042\u6f22\uff21", TextCount(3, 3, 0)),
        ("\uff71\uff9e", TextCount(2, 2, 2)),
        ("\u3000", TextCount(1, 0, 0)),
        ("\u00e9", TextCount(1, 1, 0)),
        ("\U0001f600", TextCount(1, 1, 0)),
        ("e\u0301", TextCount(2, 2, 1)),
    ],
    ids=[
        "ascii",
        "space-tab-newline",
        "wide-hiragana-kanji-fullwidth-a",
        "halfwidth-katakana",
        "ideographic-space",
        "ambiguous-e-acute",
        "emoji",
        "combining-mark",
    ],
)
def test_count_text(text: str, expected: TextCount) -> None:
    assert count_text(text) == expected
