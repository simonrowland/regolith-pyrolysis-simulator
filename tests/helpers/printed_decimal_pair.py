from __future__ import annotations

import re


def printed_decimal_pair(cell: str) -> tuple[str, str]:
    match = re.fullmatch(
        r"\s*([0-9]+[,.][0-9]+)\s*\(\s*([0-9]+[,.][0-9]+)\s*\)\s*",
        cell,
    )
    assert match is not None, cell
    return match.group(1), match.group(2)
