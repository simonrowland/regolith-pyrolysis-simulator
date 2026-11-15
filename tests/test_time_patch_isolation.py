"""Guard against tests changing clock functions shared by every thread."""

import ast
from pathlib import Path


_CLOCK_ATTRIBUTES = {"monotonic", "sleep", "time"}


def _patches_shared_clock(call: ast.Call) -> bool:
    keyword_values = {item.arg: item.value for item in call.keywords if item.arg}
    target = call.args[0] if call.args else keyword_values.get("target")
    if target is None:
        target = keyword_values.get("object")
    attribute = (
        call.args[1]
        if len(call.args) > 1
        else keyword_values.get("name")
    )
    if attribute is None:
        attribute = keyword_values.get("attribute")
    if isinstance(target, ast.Name) and target.id == "time":
        return (
            isinstance(attribute, ast.Constant)
            and attribute.value in _CLOCK_ATTRIBUTES
        )
    if isinstance(target, ast.Attribute) and target.attr == "time":
        return (
            isinstance(attribute, ast.Constant)
            and attribute.value in _CLOCK_ATTRIBUTES
        )
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        parts = target.value.split(".")
        return (
            len(parts) >= 2
            and parts[-2] == "time"
            and parts[-1] in _CLOCK_ATTRIBUTES
        )
    return False


def test_tests_do_not_patch_shared_time_module_attributes() -> None:
    tests_root = Path(__file__).parent
    violations: list[str] = []
    for path in sorted(tests_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _patches_shared_clock(node):
                violations.append(f"{path.relative_to(tests_root)}:{node.lineno}")

    assert not violations, (
        "tests must patch a module-local time namespace, not shared time "
        "attributes: " + ", ".join(violations)
    )
