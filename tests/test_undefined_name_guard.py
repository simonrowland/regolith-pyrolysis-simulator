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
        "from __future__ import annotations\n"
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


@pytest.mark.parametrize(
    ("declaration", "line"),
    [
        ("def consume(value: TypeOnly):\n    return None\n", 5),
        ("def produce() -> TypeOnly:\n    return None\n", 5),
        ("module_value: TypeOnly\n", 5),
        ("class Box:\n    value: TypeOnly\n", 6),
    ],
)
def test_eager_type_checking_annotations_are_reported(
    tmp_path: Path,
    declaration: str,
    line: int,
) -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from collections.abc import Iterable as TypeOnly\n"
        "\n"
        f"{declaration}"
    )
    path = tmp_path / "eager_annotations.py"
    path.write_text(source)

    assert undefined_names(path) == (("TypeOnly", line),)


@pytest.mark.parametrize(
    "declaration",
    [
        "def consume(value: TypeOnly):\n    return None\n",
        "def produce() -> TypeOnly:\n    return None\n",
        "module_value: TypeOnly\n",
        "class Box:\n    value: TypeOnly\n",
    ],
)
def test_postponed_type_checking_annotations_are_allowed(
    tmp_path: Path,
    declaration: str,
) -> None:
    source = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from collections.abc import Iterable as TypeOnly\n"
        "\n"
        f"{declaration}"
    )
    path = tmp_path / "postponed_annotations.py"
    path.write_text(source)

    assert undefined_names(path) == ()


def test_local_variable_annotation_is_not_evaluated(tmp_path: Path) -> None:
    path = tmp_path / "local_annotation.py"
    path.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from collections.abc import Iterable as TypeOnly\n"
        "\n"
        "def consume():\n"
        "    local_value: TypeOnly\n"
    )

    assert undefined_names(path) == ()


@pytest.mark.parametrize(
    "expression",
    [
        "[missing for value in values]",
        "{missing for value in values}",
        "{value: missing for value in values}",
        "(missing for value in values)",
    ],
)
def test_undefined_names_in_comprehensions_are_reported(
    tmp_path: Path,
    expression: str,
) -> None:
    path = tmp_path / "comprehension.py"
    path.write_text(
        "def collect(values):\n"
        f"    return {expression}\n"
    )

    assert undefined_names(path) == (("missing", 2),)


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
