import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pytoolbox.errors import InputError
from pytoolbox_gui.screens import path_field
from pytoolbox_gui.server import create_app

HX = {"HX-Request": "true"}
ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as test:
        yield test


def _resx(path: Path, *pairs: tuple[str, str]) -> Path:
    body = "".join(f'<data name="{name}"><value>{value}</value></data>' for name, value in pairs)
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?><root>{body}</root>', "utf-8")
    return path


def _post(client: TestClient, file1: str, file2: str) -> tuple[int, str]:
    response = client.post(
        "/tools/resx-compare", data={"file1": file1, "file2": file2}, headers={**ORIGIN, **HX}
    )
    return response.status_code, response.text


def _count(html: str, status: str) -> str:
    found = re.search(rf'resx-count-{status}"><dt>[^<]*</dt><dd>(\d+)</dd>', html)
    assert found is not None
    return found.group(1)


def test_page_posts_when_a_path_is_committed(client: TestClient) -> None:
    pane = client.get("/tools/resx-compare", headers=HX).text

    assert 'hx-post="/tools/resx-compare"' in pane
    assert "hx-trigger=\"change, keyup[key=='Enter']\"" in pane
    assert 'hx-sync="this:replace"' in pane
    assert 'name="file1"' in pane
    assert 'name="file2"' in pane
    assert "<button" not in pane
    assert "Enter both paths." in pane
    assert "hx-swap-oob" not in pane


def test_post_counts_each_status_and_lists_only_the_differences(
    client: TestClient, tmp_path: Path
) -> None:
    first = _resx(tmp_path / "1.resx", ("a", "Hi"), ("b", "same"), ("c", "&lt;b&gt;bold&lt;/b&gt;"))
    second = _resx(tmp_path / "2.resx", ("a", "hi"), ("b", "same"), ("d", ""))

    status, html = _post(client, str(first), str(second))

    assert status == 200
    assert [_count(html, s) for s in ("same", "differ", "only1", "only2")] == ["1", "1", "1", "1"]
    assert "Differences (3)" in html
    rows = re.findall(r'<tr class="resx-row-(\w+)">\s*<th scope="row"><code>(\w+)</code>', html)
    assert rows == [("differ", "a"), ("only1", "c"), ("only2", "d")]
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "missing" in html
    assert "empty" in html
    assert 'id="error"' in html


def test_post_without_differences_says_so(client: TestClient, tmp_path: Path) -> None:
    first = _resx(tmp_path / "1.resx", ("a", "x"))
    second = _resx(tmp_path / "2.resx", ("a", "x"))

    status, html = _post(client, f'  "{first}" ', str(second))

    assert status == 200
    assert "No differences." in html
    assert "<table" not in html


@pytest.mark.parametrize("file1", ["", "  ", '""'])
def test_post_with_an_empty_path_compares_nothing_and_clears_the_error(
    client: TestClient, tmp_path: Path, file1: str
) -> None:
    second = _resx(tmp_path / "2.resx")

    status, html = _post(client, file1, str(second))

    assert status == 200
    assert "Enter both paths." in html
    assert 'id="error"' in html
    assert _count(html, "same") == "0"


def test_post_rejects_a_relative_path(client: TestClient, tmp_path: Path) -> None:
    second = _resx(tmp_path / "2.resx")

    status, html = _post(client, "relative.resx", str(second))

    assert status == 422
    assert "File 1: use an absolute path" in html


def test_post_reports_a_missing_or_broken_file(client: TestClient, tmp_path: Path) -> None:
    good = _resx(tmp_path / "good.resx")
    broken = tmp_path / "broken.resx"
    broken.write_text("<root>", "utf-8")
    duplicate = _resx(tmp_path / "dup.resx", ("a", "1"), ("a", "2"))

    for path, message in [
        (tmp_path / "none.resx", "no such file"),
        (broken, "not valid XML"),
        (duplicate, "appears twice"),
    ]:
        status, html = _post(client, str(good), str(path))
        assert status == 422
        assert message in html


def test_path_field_drops_one_pair_of_quotes_and_spaces(tmp_path: Path) -> None:
    form = {"p": [f' "{tmp_path}" ']}

    assert path_field(form, "p", "P") == tmp_path


def test_path_field_keeps_a_lone_quote() -> None:
    with pytest.raises(InputError, match="use an absolute path"):
        path_field({"p": ['"']}, "p", "P")
