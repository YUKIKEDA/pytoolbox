from pathlib import Path

import pytest

from pytoolbox.errors import InputError, OperationError
from pytoolbox.tools.dev.resx import Entry, Status, compare_resx, read_resx

HEADER = '<?xml version="1.0" encoding="utf-8"?>'


def _resx(body: str) -> str:
    return (
        f"{HEADER}<root>"
        '<resheader name="resmimetype"><value>text/microsoft-resx</value></resheader>'
        f"{body}</root>"
    )


def _data(name: str, value: str, comment: str = "") -> str:
    note = f"<comment>{comment}</comment>" if comment else ""
    return f'<data name="{name}" xml:space="preserve"><value>{value}</value>{note}</data>'


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_bytes(text.encode(encoding))
    return path


def test_read_takes_each_data_name_and_value_and_skips_headers(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.resx", _resx(_data("Hello", "Hi") + _data("Bye", "See you")))

    assert read_resx(path) == {"Hello": "Hi", "Bye": "See you"}


def test_a_missing_or_empty_value_is_empty_text(tmp_path: Path) -> None:
    body = '<data name="A"/><data name="B"><value></value></data>' + _data("C", "c")
    path = _write(tmp_path / "a.resx", _resx(body))

    assert read_resx(path) == {"A": "", "B": "", "C": "c"}


@pytest.mark.parametrize(
    ("prefix", "encoding", "declaration"),
    [
        (b"", "utf-8", "utf-8"),
        (b"\xef\xbb\xbf", "utf-8", "utf-8"),
        (b"", "utf-16", "utf-16"),
    ],
    ids=["utf-8", "utf-8 with a bom", "utf-16 with a bom"],
)
def test_the_declaration_and_bom_decide_the_encoding(
    tmp_path: Path, prefix: bytes, encoding: str, declaration: str
) -> None:
    text = _resx(_data("Greeting", "こんにちは")).replace("utf-8", declaration)
    path = tmp_path / "a.resx"
    path.write_bytes(prefix + text.encode(encoding))

    assert read_resx(path) == {"Greeting": "こんにちは"}


def test_an_encoding_the_parser_cannot_read_is_an_input_error(tmp_path: Path) -> None:
    text = _resx(_data("Greeting", "こんにちは")).replace("utf-8", "shift_jis")
    path = _write(tmp_path / "a.resx", text, "shift_jis")

    with pytest.raises(InputError, match=r"a\.resx: the encoding shift_jis is not supported"):
        read_resx(path)


def test_a_missing_file_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no such file"):
        read_resx(tmp_path / "missing.resx")


def test_a_directory_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no such file"):
        read_resx(tmp_path)


def test_xml_that_does_not_parse_is_an_input_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "broken.resx", "<root><data></root>")

    with pytest.raises(InputError, match=r"broken\.resx: not valid XML: mismatched tag"):
        read_resx(path)


def test_a_data_without_a_name_is_an_input_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.resx", _resx("<data><value>x</value></data>"))

    with pytest.raises(InputError, match="a data element has no name"):
        read_resx(path)


def test_a_name_that_appears_twice_is_an_input_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.resx", _resx(_data("A", "1") + _data("A", "2")))

    with pytest.raises(InputError, match="the name 'A' appears twice"):
        read_resx(path)


def test_a_file_that_cannot_be_read_is_an_operation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "a.resx", _resx(""))

    def denied(_self: Path) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(OperationError, match="cannot read: Permission denied"):
        read_resx(path)


def test_compare_sorts_every_key_and_gives_each_status(tmp_path: Path) -> None:
    first = _write(
        tmp_path / "1.resx",
        _resx(_data("b", "same", "note 1") + _data("a", "Hi") + _data("c", "only here")),
    )
    second = _write(
        tmp_path / "2.resx",
        _resx(_data("a", "hi") + _data("d", "") + _data("b", "same", "note 2")),
    )

    assert compare_resx(first, second) == (
        Entry("a", "Hi", "hi", Status.DIFFER),
        Entry("b", "same", "same", Status.SAME),
        Entry("c", "only here", None, Status.ONLY1),
        Entry("d", None, "", Status.ONLY2),
    )


def test_compare_counts_spaces_as_a_difference(tmp_path: Path) -> None:
    first = _write(tmp_path / "1.resx", _resx(_data("a", "x")))
    second = _write(tmp_path / "2.resx", _resx(_data("a", "x ")))

    assert compare_resx(first, second)[0].status is Status.DIFFER


def test_compare_reports_the_broken_file(tmp_path: Path) -> None:
    good = _write(tmp_path / "good.resx", _resx(""))
    bad = _write(tmp_path / "bad.resx", "not xml")

    with pytest.raises(InputError, match=r"bad\.resx"):
        compare_resx(good, bad)
