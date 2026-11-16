"""Assertions for regression probes that must not pass by skipping."""

from collections.abc import Callable

import pytest


def _run_without_skip(call: Callable[[], object]) -> object:
    try:
        return call()
    except pytest.skip.Exception as exc:
        pytest.fail(f"regression unexpectedly skipped: {exc}", pytrace=False)


def assert_regression_completes_without_skip(call: Callable[[], object]) -> object:
    """Run a positive regression probe and fail if its body unexpectedly skips."""
    return _run_without_skip(call)


def assert_regression_raises_assertion_without_skip(
    call: Callable[[], object], *, match: str | None = None
) -> AssertionError:
    """Require an AssertionError and turn an unexpected pytest skip into failure."""
    with pytest.raises(AssertionError, match=match) as exc_info:
        _run_without_skip(call)
    return exc_info.value
