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


def test_comprehension_target_does_not_bind_module_name(tmp_path: Path) -> None:
    path = tmp_path / "comprehension_target.py"
    path.write_text(
        "values = [leaked for leaked in (1,)]\n"
        "def read():\n"
        "    return leaked\n"
    )

    assert undefined_names(path) == (("leaked", 3),)


def test_for_target_binds_module_name(tmp_path: Path) -> None:
    path = tmp_path / "for_target.py"
    path.write_text(
        "for T in (1,):\n"
        "    pass\n"
        "def read():\n"
        "    return T\n"
    )

    assert undefined_names(path) == ()


def test_with_target_rebinds_type_checking_import(tmp_path: Path) -> None:
    path = tmp_path / "with_target.py"
    path.write_text(
        "from typing import TYPE_CHECKING\n"
        "from contextlib import nullcontext\n"
        "if TYPE_CHECKING:\n"
        "    from somewhere import T\n"
        "with nullcontext() as T:\n"
        "    pass\n"
        "def read():\n"
        "    return T\n"
    )

    assert undefined_names(path) == ()


def test_bare_annotation_does_not_rebind_type_checking_import(tmp_path: Path) -> None:
    path = tmp_path / "bare_annotation.py"
    path.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from somewhere import T\n"
        "T: object\n"
        "def read():\n"
        "    return T\n"
    )

    assert undefined_names(path) == (("T", 6),)


def test_dict_comprehension_targets_do_not_bind_enclosing_scope(tmp_path: Path) -> None:
    path = tmp_path / "dict_comprehension_targets.py"
    path.write_text(
        "_OPEN_TO_CLOSE = {'(': ')', '[': ']', '{': '}'}\n"
        "_CLOSE_TO_OPEN = {v: k for k, v in _OPEN_TO_CLOSE.items()}\n"
    )

    assert undefined_names(path) == ()


def test_later_variable_annotation_uses_shared_annotation_scope(
    tmp_path: Path,
) -> None:
    path = tmp_path / "shared_annotations.py"
    path.write_text("first: int\nsecond: MissingType\n")

    assert undefined_names(path) == (("MissingType", 2),)


def test_runtime_assignment_after_type_checking_import_is_a_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime_rebinding.py"
    path.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from somewhere import T\n"
        "T = object()\n"
        "def read():\n"
        "    return T\n"
    )

    assert undefined_names(path) == ()


def test_class_type_checking_import_is_not_runtime_annotation_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "class_type_checking_annotation.py"
    path.write_text(
        "from typing import TYPE_CHECKING\n"
        "class Box:\n"
        "    if TYPE_CHECKING:\n"
        "        from somewhere import T\n"
        "    def read(self, value: T):\n"
        "        return None\n"
    )

    assert undefined_names(path) == (("T", 5),)


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


def test_augmented_assignment_requires_an_existing_module_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "augmented_assignment.py"
    path.write_text("total += 1\n")

    assert undefined_names(path) == (("total", 1),)


def test_comprehension_target_subexpressions_are_loads(tmp_path: Path) -> None:
    path = tmp_path / "comprehension_target_subexpressions.py"
    path.write_text("value = [None for slots[missing] in (1,)]\n")

    assert undefined_names(path) == (("missing", 1), ("slots", 1))


def test_closure_cannot_read_function_scope_type_checking_import(
    tmp_path: Path,
) -> None:
    path = tmp_path / "closure_type_checking_import.py"
    path.write_text(
        "from typing import TYPE_CHECKING\n"
        "def outer():\n"
        "    if TYPE_CHECKING:\n"
        "        from math import sqrt\n"
        "    def inner():\n"
        "        return sqrt(4)\n"
    )

    assert undefined_names(path) == (("sqrt", 6),)


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
