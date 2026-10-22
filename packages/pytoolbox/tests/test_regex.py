import pytest

from pytoolbox.errors import InputError
from pytoolbox.tools.dev.regex import Group, Match, match_pattern


def test_without_g_only_the_first_match_anywhere_in_the_text() -> None:
    assert match_pattern("b", "abcb") == (Match(1, 2, "b", ()),)


def test_g_returns_every_match_in_order() -> None:
    assert match_pattern("b", "abcb", all_matches=True) == (
        Match(1, 2, "b", ()),
        Match(3, 4, "b", ()),
    )


def test_no_match_is_empty() -> None:
    assert match_pattern("z", "abc", all_matches=True) == ()
    assert match_pattern("z", "abc") == ()


def test_i_ignores_case() -> None:
    assert match_pattern("A", "a") == ()
    assert match_pattern("A", "a", ignore_case=True) == (Match(0, 1, "a", ()),)


def test_m_lets_anchors_match_at_line_breaks() -> None:
    assert match_pattern("^b$", "a\nb") == ()
    assert match_pattern("^b$", "a\nb", multiline=True) == (Match(2, 3, "b", ()),)


def test_groups_are_numbered_named_and_unmatched() -> None:
    (match,) = match_pattern(r"(?P<word>[a-z]+)(\d)?(x)?", "ab1")

    assert match == Match(
        0,
        3,
        "ab1",
        (
            Group(1, "word", 0, 2, "ab"),
            Group(2, None, 2, 3, "1"),
            Group(3, None, None, None, None),
        ),
    )


def test_positions_count_code_points() -> None:
    assert match_pattern("a", "\u3042\U0001f600a") == (Match(2, 3, "a", ()),)


def test_empty_matches_are_kept() -> None:
    assert match_pattern("x*", "ab", all_matches=True) == (
        Match(0, 0, "", ()),
        Match(1, 1, "", ()),
        Match(2, 2, "", ()),
    )


def test_an_invalid_pattern_is_input_error() -> None:
    with pytest.raises(InputError, match="Invalid pattern: missing \\)"):
        match_pattern("(", "x")
