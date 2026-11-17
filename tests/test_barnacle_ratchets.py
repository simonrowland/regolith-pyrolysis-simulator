from __future__ import annotations

import ast
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_ID_SIMULATOR_CEILING = 21
SOURCE_ID_TOOLS_SCRIPTS_CEILING = 2
FINITE_HELPER_CEILING = 87
SC_388_SITE_CEILING = 30  # b261: 32 -> 30; the discovery walker no longer finds species_rail_differential._evaluate_rail_pressure_Pa or engine_binding_admission.gate_curve_memo_origin_matches (the other allow-list rows were behavior pins, not counted sites)

SC_388_ALLOWLIST = ()  # emptied in b261: engine typed-failures (4de120b56) and physics dry56/dry10 fixed every listed site

SC_388_BEHAVIOR_PINS = ()  # emptied in b261: engine typed-failures (4de120b56) and physics dry56/dry10 fixed every listed site


def _git_grep(pattern: str, *paths: str) -> list[str]:
    completed = subprocess.run(
        ["git", "grep", "-I", "-n", "-E", pattern, "--", *paths],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode in {0, 1}, completed.stderr
    return completed.stdout.splitlines()


def _source_id_expression(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "source_id") or (
        isinstance(node, ast.Attribute) and node.attr == "source_id"
    )


def _source_id_binding_name(name: str) -> bool:
    return name in {"SOURCE_ID", "SOURCE_IDS"} or name.endswith(
        ("_SOURCE_ID", "_SOURCE_IDS")
    )


def _declared_source_id_value(
    node: ast.AST, source_id_names: set[str]
) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.Name):
        return node.id in source_id_names
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return bool(node.elts) and all(
            _declared_source_id_value(item, source_id_names)
            for item in node.elts
        )
    return False


def _source_id_comparison_sites_in_source(source: str, path: str) -> list[str]:
    tree = ast.parse(source, filename=path)
    source_id_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if _source_id_binding_name(alias.name)
    }
    assignments = [
        (target, node.value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and node.value is not None
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
        and _source_id_binding_name(target.id)
    ]
    # Resolve simple aliases and string/set constants so the guard catches
    # source-id comparisons even when a local name hides the token.
    while True:
        new_names = {
            target.id
            for target, value in assignments
            if target.id not in source_id_names
            and _declared_source_id_value(value, source_id_names)
        }
        if not new_names:
            break
        source_id_names.update(new_names)

    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        if any(
            isinstance(operator, (ast.Eq, ast.NotEq, ast.In, ast.NotIn))
            and (
                _source_id_expression(left)
                and _declared_source_id_value(right, source_id_names)
                or _source_id_expression(right)
                and _declared_source_id_value(left, source_id_names)
            )
            for left, right, operator in zip(
                operands, operands[1:], node.ops
            )
        ):
            sites.append(f"{path}:{node.lineno}: {ast.unparse(node)}")
    return sites


def _source_id_comparison_sites(*paths: str) -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "--", *paths],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    sites = []
    for relpath in tracked.stdout.splitlines():
        if not relpath.endswith(".py"):
            continue
        source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        sites.extend(_source_id_comparison_sites_in_source(source, relpath))
    return sites


def _assert_count_at_most(
    label: str, matches: list[str], ceiling: int
) -> None:
    extra = len(matches) - ceiling
    assert extra <= 0, (
        f"{label} gained {extra} new/extra matching line(s) above ceiling "
        f"{ceiling}; matching lines:\n" + "\n".join(matches)
    )


def _function_source(path: str, function: str) -> str:
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function
    ]
    assert len(matches) == 1, f"expected one {path}:{function} definition"
    body = ast.get_source_segment(source, matches[0])
    assert body is not None
    return body


def _constant_value(node: ast.AST | None) -> bool:
    """Whether node is a scalar or a tuple/dict made only of constants."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, int, float, complex, bool, type(None)))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return isinstance(node.operand, ast.Constant) and isinstance(
            node.operand.value, (int, float, complex)
        )
    if isinstance(node, ast.Tuple):
        return all(_constant_value(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            _constant_value(key) and _constant_value(value)
            for key, value in zip(node.keys, node.values)
        )
    return False


def _constant_literal(node: ast.AST | None) -> bool:
    return _constant_value(node) and not (
        isinstance(node, ast.Constant) and node.value is None
    )


def _broad_exception(node: ast.expr | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in {"Exception", "BaseException"}
    return isinstance(node, ast.Tuple) and any(
        _broad_exception(item) for item in node.elts
    )


def _function_node(tree: ast.AST, lineno: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= lineno <= node.end_lineno
    ]
    return min(functions, key=lambda node: node.end_lineno - node.lineno) if functions else None


def _mapping_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Subscript):
        return _mapping_names(node.value)
    return set()


def _returns_mapping(function: ast.AST | None, names: set[str]) -> bool:
    if not names or not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return any(
        isinstance(node, ast.Return)
        and any(
            isinstance(value, ast.Name) and value.id in names
            for value in ast.walk(node.value)
        )
        for node in ast.walk(function)
    )


def _sc388_sites(root: Path = REPO_ROOT) -> list[str]:
    """Find broad catches that publish literal fallbacks as ordinary results.

    Typed refusal/failure objects, re-raises and None are excluded. Literal
    strings, numbers and constant-only containers count, as do literal values
    written into a mapping with setdefault or subscript assignment.
    """
    sites: list[str] = []
    for directory in ("simulator", "engines"):
        for path in sorted((root / directory).rglob("*.py")):
            if any(part in {"test", "tests"} for part in path.parts):
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
                if not _broad_exception(handler.type):
                    continue
                function = _function_node(tree, handler.lineno)
                returns_literal = any(
                    isinstance(node, ast.Return) and _constant_literal(node.value)
                    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
                )
                mapping_names: set[str] = set()
                for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "setdefault"
                        and len(node.args) >= 2
                        and _constant_value(node.args[1])
                    ):
                        mapping_names.update(_mapping_names(node.func.value))
                    elif isinstance(node, ast.Assign) and _constant_value(node.value):
                        for target in node.targets:
                            if isinstance(target, ast.Subscript):
                                mapping_names.update(_mapping_names(target.value))
                mutates_mapping_with_literal = _returns_mapping(function, mapping_names)
                if returns_literal or mutates_mapping_with_literal:
                    relpath = path.relative_to(root).as_posix()
                    function_name = function.name if function else "<module>"
                    sites.append(f"{relpath}:{function_name}:{handler.lineno}")
    return sites


def _has_except_return(path: str, function: str, value: str) -> bool:
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path)
    function_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function
    ]
    assert len(function_nodes) == 1, f"expected one {path}:{function} definition"
    return any(
        isinstance(handler, ast.ExceptHandler)
        and _broad_exception(handler.type)
        and any(
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and node.value.value == value
            for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
        )
        for handler in ast.walk(function_nodes[0])
    )


def test_source_id_comparisons_are_decrease_only() -> None:
    simulator = _source_id_comparison_sites("simulator")
    tools_scripts = _source_id_comparison_sites("tools", "scripts")

    _assert_count_at_most(
        "source_id comparisons in simulator", simulator, SOURCE_ID_SIMULATOR_CEILING
    )
    _assert_count_at_most(
        "source_id comparisons in tools/scripts",
        tools_scripts,
        SOURCE_ID_TOOLS_SCRIPTS_CEILING,
    )


def test_source_id_comparison_ratchet_catches_literals_constants_and_membership() -> None:
    source = """
from simulator.battery.generators.sample import SOURCE_ID as sample_source
from simulator.battery.source_declarations import HASHIMOTO_SOURCE_ID as review_source
SOURCE_ID = "review-source"
REVIEW_SOURCE_IDS = {"review-source"}
_SAMPLE_SOURCE_ID = "sample-source"
def matches(source_id, observation):
    return (
        source_id == "literal-source"
        or observation.source_id == _SAMPLE_SOURCE_ID
        or source_id in {"first-source", "second-source"}
        or source_id == sample_source
        or source_id == SOURCE_ID
        or source_id in REVIEW_SOURCE_IDS
        or source_id == review_source
    )
"""
    sites = _source_id_comparison_sites_in_source(source, "synthetic.py")

    assert len(sites) == 7, "all source-id comparison forms must enter the ratchet"


def _imported_names_by_module(tree: ast.AST) -> dict[tuple[str, str], str | None]:
    return {
        (alias.asname or alias.name, alias.name): node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def _source_id_from_owner(module: str) -> str:
    owner_path = REPO_ROOT / Path(*module.split("."))
    tree = ast.parse(owner_path.with_suffix(".py").read_text(encoding="utf-8"))
    value = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SOURCE_ID"
            for target in node.targets
        )
    )
    assert isinstance(value, ast.Constant) and isinstance(value.value, str)
    return value.value


def test_compilation_source_ids_reuse_reference_data_owners() -> None:
    declaration_path = REPO_ROOT / "simulator/battery/source_declarations.py"
    declaration_tree = ast.parse(declaration_path.read_text(encoding="utf-8"))
    owners = {
        "B1259_SOURCE_ID": (
            "simulator.reference_data.robie_waldbaum_1968_usgs_b1259_loader"
        ),
        "B1452_SOURCE_ID": (
            "simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader"
        ),
    }
    declaration_imports = _imported_names_by_module(declaration_tree)
    assert all(
        declaration_imports.get((name, "SOURCE_ID")) == module
        for name, module in owners.items()
    )

    declarations = next(
        node.value
        for node in declaration_tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "SOURCE_DECLARATIONS"
    )
    assert isinstance(declarations, ast.Dict)
    declaration_keys = {
        key.id for key in declarations.keys if isinstance(key, ast.Name)
    }
    assert set(owners) <= declaration_keys

    migration_path = REPO_ROOT / "simulator/battery/migrate.py"
    migration_tree = ast.parse(migration_path.read_text(encoding="utf-8"))
    migration_imports = _imported_names_by_module(migration_tree)
    migration_owners = {
        "_B1544_SOURCE_ID": (
            "simulator.reference_data.hemingway_haas_robinson_1982_usgs_b1544_loader"
        ),
        "_B1259_SOURCE_ID": owners["B1259_SOURCE_ID"],
        "_B1452_SOURCE_ID": owners["B1452_SOURCE_ID"],
    }
    assert all(
        migration_imports.get((local_name, "SOURCE_ID")) == module
        for local_name, module in migration_owners.items()
    )
    owner_values = {
        _source_id_from_owner(module) for module in migration_owners.values()
    }
    assert all(
        owner_values.isdisjoint(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        for tree in (declaration_tree, migration_tree)
    )


def test_finite_numeric_helpers_are_frozen() -> None:
    matches = _git_grep(
        r"^[[:space:]]*def (_finite|_is_number)",
        "simulator",
        "engines",
        "tools",
        "scripts",
        "web",
    )
    _assert_count_at_most(
        "finite/_is_number helpers", matches, FINITE_HELPER_CEILING
    )


def test_sc388_allowlist_only_shrinks() -> None:
    """Catch broad handlers that publish literal results after exceptions.

    Literal strings, numbers and constant-only containers count; mapping
    literals published with setdefault or subscript assignment count too.
    Re-raises, typed refusal/failure objects and None are excluded.
    """
    listed_sites = [
        (path, function) for path, function, _lane, _task in SC_388_ALLOWLIST
    ]
    entries = set(listed_sites)
    assert len(entries) == len(listed_sites), "SC-388 allow-list has duplicate sites"
    # The allow-list may be empty: b261 fixed every listed site. The guard that
    # remains is the discovered-site ceiling below, which only shrinks.
    assert len(SC_388_BEHAVIOR_PINS) == len(entries), "every allow-listed site needs a behavior pin"
    sites = _sc388_sites()
    print(f"SC-388 discovered count: {len(sites)} (ceiling {SC_388_SITE_CEILING})")
    extra_sites = len(sites) - SC_388_SITE_CEILING
    assert extra_sites <= 0, (
        f"SC-388 discovered {len(sites)} site(s), above ceiling "
        f"{SC_388_SITE_CEILING}; discovered sites: " + "; ".join(sites)
    )

    live_fallbacks = set()
    for path, function, fragments in SC_388_BEHAVIOR_PINS:
        if fragments[0] == "except-return":
            live = _has_except_return(path, function, fragments[1])
        else:
            body = _function_source(path, function)
            live = all(fragment in body for fragment in fragments)
        if live:
            live_fallbacks.add((path, function))
    stale = entries - live_fallbacks
    assert not stale, "stale allow-list entry: remove it: " + "; ".join(
        f"{path}:{function}" for path, function in sorted(stale)
    )
    unlisted = live_fallbacks - entries
    assert not unlisted, (
        "new live SC-388 fallback missing from allow-list: "
        + "; ".join(f"{path}:{function}" for path, function in sorted(unlisted))
    )
