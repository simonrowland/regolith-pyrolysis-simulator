from __future__ import annotations

from pathlib import Path

import pytest

from tests.undefined_name_check import undefined_names


@pytest.mark.parametrize(
    ("header", "guard"),
    [
        ("from typing import TYPE_CHECKING", "TYPE_CHECKING"),
        ("import typing", "typing.TYPE_CHECKING"),
    ],
)
def test_type_checking_import_used_at_runtime_is_reported(
    tmp_path: Path,
    header: str,
    guard: str,
) -> None:
    source = (
        f"{header}\n"
        f"if {guard}:\n"
        "    from math import sqrt\n"
        "\n"
        "def calculate():\n"
        "    return sqrt(4)\n"
    )
    path = tmp_path / "type_checking_runtime.py"
    path.write_text(source)

    assert undefined_names(path) == (("sqrt", 6),)


@pytest.mark.parametrize(
    ("header", "guard"),
    [
        ("from typing import TYPE_CHECKING", "TYPE_CHECKING"),
        ("import typing", "typing.TYPE_CHECKING"),
    ],
)
def test_type_checking_import_used_only_in_annotation_is_allowed(
    tmp_path: Path,
    header: str,
    guard: str,
) -> None:
    source = (
        f"{header}\n"
        f"if {guard}:\n"
        "    from collections.abc import Iterable\n"
        "\n"
        "def consume(values: Iterable):\n"
        "    return None\n"
    )
    path = tmp_path / "type_checking_annotation.py"
    path.write_text(source)

    assert undefined_names(path) == ()


def test_undefined_load_line_comes_from_its_own_scope(tmp_path: Path) -> None:
    path = tmp_path / "scope_line.py"
    path.write_text(
        "def sibling():\n"
        "    ChemistryIntent = object()\n"
        "    return ChemistryIntent\n"
        "\n"
        "def unbound_scope():\n"
        "    return ChemistryIntent\n"
    )

    assert undefined_names(path) == (("ChemistryIntent", 6),)


def test_simulator_and_web_have_no_undefined_names() -> None:
    repo = Path(__file__).resolve().parents[1]
    violations = []
    for root_name in ("simulator", "web"):
        for path in sorted((repo / root_name).rglob("*.py")):
            relative = path.relative_to(repo).as_posix()
            violations.extend(
                f"{relative}:{line}: undefined name {name}"
                for name, line in undefined_names(path)
            )

    assert not violations, "\n".join(violations)
