import pytest

from pytoolbox.errors import InputError, OperationError, PytoolboxError
from pytoolbox.registry import System, Tool, check_unique, find, group
from pytoolbox.results import BatchResult

TEXT = Tool("text-count", System.DEVELOPER, "Text", "Count characters.")
REGEX = Tool("regex", System.DEVELOPER, "Text", "Match a pattern.")
CODE = Tool("resx-compare", System.DEVELOPER, "Code", "Compare resx files.")
CLEAN = Tool("cache-clean", System.OPERATIONS, "Cleanup", "Delete named caches.")


def test_errors_share_a_base() -> None:
    assert issubclass(InputError, PytoolboxError)
    assert issubclass(OperationError, PytoolboxError)


def test_batch_result_defaults_to_no_failures() -> None:
    assert BatchResult(succeeded=3) == BatchResult(3, (), cancelled=False)


def test_check_unique_accepts_distinct_names() -> None:
    check_unique([TEXT, REGEX, CLEAN])


def test_check_unique_refuses_a_repeated_name() -> None:
    with pytest.raises(ValueError, match="Duplicate tool name: regex"):
        check_unique([REGEX, TEXT, REGEX])


def test_find_returns_the_named_tool() -> None:
    assert find([TEXT, CLEAN], "cache-clean") is CLEAN


def test_find_refuses_an_unknown_name() -> None:
    with pytest.raises(InputError, match="Unknown tool: nope"):
        find([TEXT], "nope")


def test_group_orders_systems_by_the_enum_and_keeps_tool_order() -> None:
    grouped = group([CLEAN, TEXT, CODE, REGEX])

    assert list(grouped) == [System.DEVELOPER, System.OPERATIONS]
    assert grouped[System.DEVELOPER] == {"Text": [TEXT, REGEX], "Code": [CODE]}
    assert grouped[System.OPERATIONS] == {"Cleanup": [CLEAN]}


def test_group_leaves_out_a_system_without_tools() -> None:
    assert group([TEXT]) == {System.DEVELOPER: {"Text": [TEXT]}}
    assert group([]) == {}
