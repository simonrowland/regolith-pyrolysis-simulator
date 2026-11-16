"""Guard against tests changing clock functions shared by every thread."""

import ast
from pathlib import Path


_CLOCK_ATTRIBUTES = {"monotonic", "sleep", "time"}
_MUTATION_CALLS = {
    ("monkeypatch", "setattr"),
    ("setattr",),
    ("patch",),
    ("patch", "object"),
    ("mock", "patch"),
    ("mock", "patch", "object"),
    ("unittest", "mock", "patch"),
    ("unittest", "mock", "patch", "object"),
}


def _call_path(expression: ast.expr) -> tuple[str, ...]:
    parts = []
    while isinstance(expression, ast.Attribute):
        parts.append(expression.attr)
        expression = expression.value
    if isinstance(expression, ast.Name):
        parts.append(expression.id)
    return tuple(reversed(parts))


def _patches_shared_clock(call: ast.Call) -> bool:
    if _call_path(call.func) not in _MUTATION_CALLS:
        return False

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


def _first_call(source: str) -> ast.Call:
    tree = ast.parse(source)
    return next(node for node in ast.walk(tree) if isinstance(node, ast.Call))


def test_clock_patch_matcher_detects_mutation_forms() -> None:
    sources = (
        'monkeypatch.setattr(module.time, "monotonic", fake)',
        'monkeypatch.setattr("module.time.sleep", fake)',
        'setattr(module.time, "time", fake)',
        'mock.patch.object(module.time, "sleep", fake)',
        'patch.object(module.time, "monotonic", fake)',
        'mock.patch("module.time.monotonic", fake)',
        'unittest.mock.patch("module.time.sleep", fake)',
        'unittest.mock.patch.object(module.time, "monotonic", fake)',
        'patch("time.monotonic", fake)',
        'monkeypatch.setattr(target=module.time, name="sleep", value=fake)',
    )
    assert all(_patches_shared_clock(_first_call(source)) for source in sources)


def test_clock_patch_matcher_ignores_reads_and_other_replacements() -> None:
    sources = (
        'getattr(time, "monotonic")',
        'hasattr(module.time, "sleep")',
        'resolve_name("time.monotonic")',
        'call(module.time, "monotonic")',
        'monkeypatch.setattr(module, "time", fake_time)',
    )
    assert not any(
        _patches_shared_clock(_first_call(source)) for source in sources
    )


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
