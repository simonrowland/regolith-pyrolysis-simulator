from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tests.literature_extract_test_helpers import iter_locators


def assert_all_physical_locators(document: Mapping[str, Any]) -> None:
    """Require every nested source locator to use one-based physical pages."""

    for locator in iter_locators(document):
        page = locator.get("page")
        index = locator.get("pdf_page_index")
        assert isinstance(page, int) and not isinstance(page, bool)
        assert isinstance(index, int) and not isinstance(index, bool)
        assert page == index + 1, locator
