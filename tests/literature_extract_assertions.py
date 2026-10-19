from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def assert_all_physical_locators(document: Mapping[str, Any]) -> None:
    """Require every nested source locator to use one-based physical pages."""

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            locator = value.get("locator")
            if isinstance(locator, Mapping):
                page = locator.get("page")
                index = locator.get("pdf_page_index")
                assert isinstance(page, int) and not isinstance(page, bool)
                assert isinstance(index, int) and not isinstance(index, bool)
                assert page == index + 1, locator
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(document)
