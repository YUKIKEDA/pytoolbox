import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from pytoolbox.tools.dev.regex import Match
from pytoolbox_gui import screens
from pytoolbox_gui.screens import Segment, checkbox, highlight
from pytoolbox_gui.server import create_app

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as test:
        yield test


def _post(client: TestClient, **fields: str) -> str:
    response = client.post("/tools/regex", data=fields, headers={**ORIGIN, **HX})
    assert response.status_code == 200
    return response.text


def _marks(html: str) -> list[str]:
    return re.findall(r"<mark [^>]*>(.*?)</mark>", html)


def test_page_matches_on_every_input_and_starts_empty(client: TestClient) -> None:
    pane = client.get("/tools/regex", headers=HX).text

    assert 'hx-post="/tools/regex"' in pane
    assert 'hx-trigger="input, submit"' in pane
    assert 'hx-sync="this:replace"' in pane
    for name in ("pattern", "global", "ignore_case", "multiline", "text"):
        assert f'name="{name}"' in pane
    assert "Matches (0)" in pane
    assert "hx-swap-oob" not in pane


def test_post_highlights_the_first_match_without_g(client: TestClient) -> None:
    html = _post(client, pattern="b", text="abcb")

    assert _marks(html) == ["b"]
    assert '<pre class="regex-text">a<mark' in html
    assert "</mark>cb</pre>" in html
    assert "Matches (1)" in html


def test_post_with_g_lists_each_match_and_its_groups(client: TestClient) -> None:
    html = _post(client, pattern=r"(?P<d>\d)(x)?", text="a1b2", **{"global": "on"})

    assert _marks(html) == ["1", "2"]
    assert "Matches (2)" in html
    assert "Group 1 <code>d</code>" in html
    assert html.count("unmatched") == 2
    assert "regex-hit-odd" in html
    assert "regex-hit-even" in html


def test_post_applies_i_and_m(client: TestClient) -> None:
    fields = {"pattern": "^a$", "text": "b\r\nA"}

    assert _marks(_post(client, **fields)) == []
    assert _marks(_post(client, **fields, ignore_case="on", multiline="on")) == ["A"]


def test_post_escapes_the_text(client: TestClient) -> None:
    html = _post(client, pattern="<b>", text="<b>&")

    assert _marks(html) == ["&lt;b&gt;"]
    assert "&amp;</pre>" in html


def test_post_clears_an_earlier_error(client: TestClient) -> None:
    html = _post(client, pattern="a", text="a")

    assert '<div id="error" class="error-area" aria-live="polite" hx-swap-oob="true"></div>' in html


def test_post_lists_positions_and_marks_an_empty_match(client: TestClient) -> None:
    html = _post(client, pattern="x*", text="a")

    assert "Match 1" in html
    assert "0\u20130" in html
    assert '<span class="regex-quiet">empty</span>' in html
    assert re.search(r"<mark [^>]*></mark>a</pre>", html)


def test_an_invalid_pattern_is_422(client: TestClient) -> None:
    response = client.post("/tools/regex", data={"pattern": "("}, headers={**ORIGIN, **HX})

    assert response.status_code == 422
    assert "Invalid pattern" in response.text


def test_closing_the_app_stops_the_regex_worker() -> None:
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        client.post("/tools/regex", data={"pattern": "a", "text": "a"}, headers=ORIGIN)
        process = screens._regex_worker._process
        assert process is not None
    assert screens._regex_worker._process is None
    process.join(5)
    assert not process.is_alive()


def test_a_stuck_match_times_out_and_the_next_one_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screens, "MATCH_TIMEOUT", 0.5)
    stuck = {"pattern": "(a+)+$", "text": "a" * 40 + "b"}

    response = client.post("/tools/regex", data=stuck, headers={**ORIGIN, **HX})

    assert response.status_code == 500
    assert "Timed out after 0.5 s" in response.text
    assert _marks(_post(client, pattern="b", text="ab")) == ["b"]


def test_highlight_splits_the_text_around_each_match() -> None:
    matches = (Match(1, 2, "b", ()), Match(2, 3, "c", ()), Match(4, 4, "", ()))

    assert highlight("abcde", matches) == (
        Segment("a", None),
        Segment("b", 1),
        Segment("c", 2),
        Segment("d", None),
        Segment("", 3),
        Segment("e", None),
    )


def test_highlight_without_matches_is_the_text() -> None:
    assert highlight("ab", ()) == (Segment("ab", None),)
    assert highlight("", ()) == ()


def test_checkbox_is_checked_when_the_field_is_sent() -> None:
    assert checkbox({"global": ["on"]}, "global")
    assert not checkbox({}, "global")
