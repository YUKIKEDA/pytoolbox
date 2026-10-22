from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from pytoolbox_gui.screens import text_field
from pytoolbox_gui.server import create_app

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as test:
        yield test


def _counts(html: str) -> list[str]:
    return [part.partition("</dd>")[0] for part in html.split("<dd>")[1:]]


def test_page_posts_each_input_and_starts_at_zero(client: TestClient) -> None:
    pane = client.get("/tools/text-count", headers=HX).text

    assert 'hx-post="/tools/text-count"' in pane
    assert 'hx-trigger="input"' in pane
    assert 'hx-sync="this:replace"' in pane
    assert _counts(pane) == ["0", "0", "0"]


def test_post_returns_the_three_counts(client: TestClient) -> None:
    response = client.post(
        "/tools/text-count", data={"text": "ab \u3042"}, headers={**ORIGIN, **HX}
    )

    assert response.status_code == 200
    assert "<nav" not in response.text
    assert _counts(response.text) == ["4", "3", "3"]


def test_post_counts_a_textarea_line_break_as_one_character(client: TestClient) -> None:
    response = client.post("/tools/text-count", data={"text": "a\r\nb"}, headers=ORIGIN)

    assert _counts(response.text) == ["3", "2", "2"]


def test_post_clears_an_earlier_error_and_the_page_does_not(client: TestClient) -> None:
    response = client.post("/tools/text-count", data={"text": "a"}, headers=ORIGIN)

    assert 'id="error"' in response.text
    assert 'hx-swap-oob="true"' in response.text
    assert "hx-swap-oob" not in client.get("/tools/text-count", headers=HX).text


def test_post_without_the_field_counts_empty_text(client: TestClient) -> None:
    response = client.post("/tools/text-count", headers=ORIGIN)

    assert _counts(response.text) == ["0", "0", "0"]


def test_text_field_takes_the_last_value() -> None:
    assert text_field({"text": ["a", "b"]}, "text") == "b"
