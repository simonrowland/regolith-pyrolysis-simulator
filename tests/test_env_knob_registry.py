"""Keep production environment reads registered.

The AST scanner resolves the environment-key forms used by production. Exotic
forms such as ``getattr(os.environ, "get")`` and ``dict(os.environ)["KEY"]``
are deliberately left to the ``environ``/``getenv`` token ceiling backstop
below. That token count catches newly added occurrences but cannot see a later
`.get()` on an alias created by ``dict(os.environ)`` without another token.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = ("simulator/", "engines/", "web/", "tools/", "scripts/")
PRODUCTION_FILES = ("app.py",)
OPERATOR_DOC = ROOT / "docs" / "configuration.md"
ENV_ACCESS_TOKEN_CEILING = 91
ENV_ACCESS_TOKEN = re.compile(r"\b(?:environ|getenv)\b")

# Internal means a probe, diagnostic, debug switch, or harness control. Keep
# each reason short and specific: these names are not normal app configuration.
KNOWN_INTERNAL_ENV = {
    "FIDELITY_DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH": "Enables internal analytical-model comparison in the fidelity diagnostic harness.",
    "FIDELITY_DIAGNOSTIC_STUB_HIGH": "Legacy alias used only by the fidelity diagnostic harness.",
    "FIDELITY_HIGH_BACKEND": "Selects a backend for the fidelity DOE harness.",
    "FIDELITY_HIGH_HOURS": "Sets run duration in the fidelity DOE harness.",
    "FIDELITY_SKIP_PROFILE_CONSTRAINTS": "Disables profile constraints in the fidelity DOE harness.",
    "DISPLAY": "Removed from MAGEMin subprocess environments to prevent display discovery.",
    "GOALFLIGHT_HOSTNAME": "Supplies a host label to an internal battery diagnostic.",
    "HOSTNAME": "Fallback host label for an internal battery diagnostic.",
    "MAGEMIN_PROBE_BINARY": "Selects the binary for a targeted MAGEMin hang probe.",
    "MAGEMIN_PROBE_OUTPUT_DIR": "Routes output from a targeted MAGEMin hang probe.",
    "MAGEMIN_PROBE_SCAN_ID": "Labels output from a targeted MAGEMin hang probe.",
    "MPLCONFIGDIR": "Selects a matplotlib cache for an internal profiling script.",
    "REGOLITH_DEBUG_FEEDSTOCKS": "Exposes debug-only feedstocks in the development UI.",
    "REGOLITH_ENGINE_POOL_DEBUG": "Prints internal engine-worker lifecycle diagnostics.",
    "REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP": "Internal worker lifecycle handoff flag.",
    "REGOLITH_OPTIMIZER_CHILD_PID_LOG": "Internal child-process tracking log path.",
    "REGOLITH_OPTIMIZER_WARM_WORKERS": "Controls optimizer worker reuse, an internal performance setting.",
    "REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR": "Internal worker output handoff path.",
    "REGOLITH_PARSED_YAML_CACHE_DIR": "Selects the implementation cache directory for parsed YAML.",
    "REGOLITH_REPO_ROOT": "Overrides the repository path for an internal cache validation script.",
    "REGOLITH_STUDIO_HOST": "Labels the Studio host in an internal battery scoring report.",
    "REGOLITH_THERMOENGINE_LOG_DIR": "Routes native engine diagnostics to a log directory.",
    "REGOLITH_VAPOROCK_SESSION_WARM": "Enables internal VapoRock worker reuse for test and diagnostic workloads.",
    "TMPDIR": "Selects temporary files for internal scripts and subprocesses.",
    "XDG_CACHE_HOME": "Selects XDG cache storage for the parsed-YAML cache and a profiling script.",
}


def _literal(node: ast.AST, names: dict[str, object]) -> object | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.Starred):
        return _literal(node.value, names)
    if isinstance(node, ast.DictComp) and len(node.generators) == 1:
        generator = node.generators[0]
        if (
            isinstance(generator.target, ast.Name)
            and isinstance(node.key, ast.Name)
            and node.key.id == generator.target.id
        ):
            return _literal(generator.iter, names)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_literal(item, names) for item in node.elts]
        if any(value is None for value in values):
            return None
        return tuple(item for value in values for item in (value if isinstance(value, tuple) else (value,)))
    return None


def _module_name(path: str) -> str:
    return path[:-3].replace("/", ".") if path.endswith(".py") else path


def _module_constants(tree: ast.Module) -> dict[str, object]:
    constants: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            value = _literal(node.value, constants)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    constants[target.id] = value
    return constants


def _scope_functions(tree: ast.Module) -> dict[int, ast.AST]:
    functions: dict[int, ast.AST] = {}

    def visit(node: ast.AST, function: ast.AST | None = None) -> None:
        if function is not None:
            functions[id(node)] = function
        for child in ast.iter_child_nodes(node):
            next_function = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            visit(child, next_function)

    visit(tree)
    return functions


def _static_bindings(
    scope: ast.AST,
    names: dict[str, object],
    function_values: dict[str, object],
    call_bindings: dict[str, list[object]],
) -> dict[str, list[object]]:
    bindings: dict[str, list[object]] = {}
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for parameter, values in zip(scope.args.args, call_bindings.get(scope.name, [])):
            bindings[parameter.arg] = [values]

    def bind(target: ast.AST, value: object | None) -> None:
        if isinstance(target, ast.Name):
            bindings.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, tuple):
            for item, item_value in zip(target.elts, value):
                bind(item, item_value)

    def iter_value(node: ast.AST, local_names: dict[str, object]) -> object | None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "items"
            and isinstance(node.func.value, ast.Name)
        ):
            return local_names.get(node.func.value.id)
        return _literal(node, local_names)

    def bound_names() -> dict[str, object]:
        resolved: dict[str, object] = {}
        for key, values in bindings.items():
            items: list[str] = []
            for value in values:
                if isinstance(value, str):
                    items.append(value)
                elif isinstance(value, tuple):
                    items.extend(item for item in value if isinstance(item, str))
                else:
                    resolved[key] = None
                    break
            else:
                resolved[key] = tuple(items)
        return resolved

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, (ast.Assign, ast.AnnAssign)) and child.value is not None:
                local_names = bound_names()
                value = _literal(child.value, {**names, **local_names})
                if value is None and isinstance(child.value, ast.Call) and isinstance(child.value.func, ast.Name):
                    value = function_values.get(child.value.func.id)
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    bind(target, value)
            elif isinstance(child, (ast.For, ast.comprehension)):
                local_names = bound_names()
                if (
                    isinstance(child.iter, ast.Call)
                    and isinstance(child.iter.func, ast.Attribute)
                    and child.iter.func.attr == "items"
                    and isinstance(child.iter.func.value, ast.Name)
                    and isinstance(local_names.get(child.iter.func.value.id), tuple)
                    and isinstance(child.target, (ast.Tuple, ast.List))
                    and len(child.target.elts) == 2
                ):
                    bind(child.target.elts[0], local_names[child.iter.func.value.id])
                    bind(child.target.elts[1], None)
                else:
                    bind(child.target, iter_value(child.iter, {**names, **local_names}))
            visit(child)

    visit(scope)
    return bindings


def _environment_aliases(tree: ast.Module) -> tuple[set[str], set[str], set[str]]:
    os_names = {"os"}
    environ_names: set[str] = set()
    getenv_names: set[str] = set()

    def is_os_environ(node: ast.AST) -> bool:
        if isinstance(node, ast.IfExp):
            return is_os_environ(node.body) or is_os_environ(node.orelse)
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id in os_names
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_names.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "environ":
                    environ_names.add(alias.asname or alias.name)
                elif alias.name == "getenv":
                    getenv_names.add(alias.asname or alias.name)

    def is_environment_mapping(node: ast.AST) -> bool:
        return (
            is_os_environ(node)
            or isinstance(node, ast.Name)
            and node.id in environ_names
            or isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "copy"
            and is_environment_mapping(node.func.value)
        )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            if is_environment_mapping(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in environ_names:
                        environ_names.add(target.id)
                        changed = True
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        function = functions.get(node.func.id)
        if function is None:
            continue
        for index, parameter in enumerate(function.args.args):
            if index < len(node.args) and is_os_environ(node.args[index]):
                environ_names.add(parameter.arg)
    return os_names, environ_names, getenv_names


def _scan_source(
    source: str,
    source_path: str = "<scratch-module>",
    imported_names: dict[str, object] | None = None,
) -> set[str]:
    tree = ast.parse(source, filename=source_path)
    constants = {**(imported_names or {}), **_module_constants(tree)}
    function_values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            returns = [
                value
                for item in ast.walk(node)
                if isinstance(item, ast.Return)
                and (value := _literal(item.value, constants)) is not None
            ]
            if returns and all(value == returns[0] for value in returns):
                function_values[node.name] = returns[0]
    functions = _scope_functions(tree)
    initial_bindings = {
        id(function): _static_bindings(function, constants, function_values, {})
        for function in set(functions.values())
    }
    function_nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    os_names, environ_names, getenv_names = _environment_aliases(tree)

    def is_environ(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Name) and node.id in environ_names
        ) or (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id in os_names
        ) or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "copy"
            and is_environ(node.func.value)
        )

    def key_argument(call: ast.Call) -> ast.AST | None:
        if call.args:
            return call.args[0]
        return next((kw.value for kw in call.keywords if kw.arg == "key"), None)

    key_parameter_indexes: dict[str, set[int]] = {}
    for read_node in ast.walk(tree):
        key_node: ast.AST | None = None
        if isinstance(read_node, ast.Subscript) and isinstance(read_node.ctx, ast.Load) and is_environ(read_node.value):
            key_node = read_node.slice
        elif isinstance(read_node, ast.Call) and isinstance(read_node.func, ast.Attribute):
            if is_environ(read_node.func.value) and read_node.func.attr in {"get", "setdefault", "pop"}:
                key_node = key_argument(read_node)
            elif read_node.func.attr == "getenv" and isinstance(read_node.func.value, ast.Name) and read_node.func.value.id in os_names:
                key_node = key_argument(read_node)
        elif isinstance(read_node, ast.Call) and isinstance(read_node.func, ast.Name) and read_node.func.id in getenv_names:
            key_node = key_argument(read_node)
        function = functions.get(id(read_node))
        if isinstance(key_node, ast.Name) and isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for index, parameter in enumerate(function.args.args):
                if parameter.arg == key_node.id:
                    key_parameter_indexes.setdefault(function.name, set()).add(index)

    call_bindings: dict[str, list[object]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        target = function_nodes.get(node.func.id)
        if target is None or not node.args:
            continue
        caller = functions.get(id(node))
        caller_bindings = initial_bindings.get(id(caller), {}) if caller else {}
        caller_names = {
            name: tuple(
                item
                for value in values
                for item in (value if isinstance(value, tuple) else (value,))
                if isinstance(item, str)
            )
            for name, values in caller_bindings.items()
        }
        resolved_args = [
            _literal(arg, {**constants, **caller_names})
            for arg in node.args
        ]
        unresolved_key_indexes = key_parameter_indexes.get(node.func.id, set()) & {
            index for index, value in enumerate(resolved_args) if value is None
        }
        if unresolved_key_indexes:
            param_index = min(unresolved_key_indexes)
            param_name = target.args.args[param_index].arg
            raise AssertionError(
                f"{source_path}:{node.lineno}: unresolved environment key argument "
                f"{param_name!r} supplied to {node.func.id}"
            )
        if any(value is None for value in resolved_args):
            continue
        previous = call_bindings.get(node.func.id)
        if previous is None:
            call_bindings[node.func.id] = resolved_args
        else:
            for index, value in enumerate(resolved_args):
                if index >= len(previous):
                    previous.append(value)
                else:
                    left = previous[index]
                    previous[index] = tuple(
                        item
                        for candidate in (left, value)
                        for item in (candidate if isinstance(candidate, tuple) else (candidate,))
                    )
    bindings_by_function = {
        id(function): _static_bindings(function, constants, function_values, call_bindings)
        for function in set(functions.values())
    }
    reads: set[str] = set()

    for node in ast.walk(tree):
        key_node: ast.AST | None = None
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and is_environ(node.value):
            key_node = node.slice
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if is_environ(node.func.value) and node.func.attr in {"get", "setdefault", "pop"}:
                key_node = key_argument(node)
            elif node.func.attr == "getenv" and isinstance(node.func.value, ast.Name) and node.func.value.id in os_names:
                key_node = key_argument(node)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in getenv_names:
                key_node = key_argument(node)
            elif node.func.id in os_names and len(node.args) >= 1:
                if node.func.id != "os":
                    continue
        elif isinstance(node, ast.Compare) and any(is_environ(item) for item in node.comparators):
            key_node = node.left

        if key_node is None:
            continue
        if isinstance(key_node, ast.Name):
            function = functions.get(id(node))
            scope_bindings = bindings_by_function.get(id(function), {}) if function else {}
            values = scope_bindings.get(key_node.id)
            if values:
                resolved: list[str] = []
                for value in values:
                    if isinstance(value, tuple):
                        resolved.extend(item for item in value if isinstance(item, str) and item)
                    elif isinstance(value, str) and value:
                        resolved.append(value)
                    else:
                        raise AssertionError(
                            f"{source_path}:{node.lineno}: unresolved environment key {key_node.id!r}"
                        )
                reads.update(resolved)
                continue
            if key_node.id in constants:
                value = constants[key_node.id]
            else:
                raise AssertionError(
                    f"{source_path}:{node.lineno}: unresolved environment key {key_node.id!r}"
                )
        else:
            value = _literal(key_node, constants)
        if isinstance(value, str) and value:
            reads.add(value)
        elif isinstance(value, tuple):
            reads.update(item for item in value if isinstance(item, str) and item)
        else:
            raise AssertionError(
                f"{source_path}:{node.lineno}: unresolved environment key "
                f"{ast.unparse(key_node)}"
            )
    return reads


def _production_python_paths() -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.split(b"\0")
    return [
        raw.decode()
        for raw in tracked
        if raw
        and raw.decode().endswith(".py")
        and (raw.decode() in PRODUCTION_FILES or raw.decode().startswith(PRODUCTION_ROOTS))
    ]


def _production_env_reads() -> set[str]:
    paths = _production_python_paths()
    trees = {path: ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path) for path in paths}
    module_constants = {_module_name(path): _module_constants(tree) for path, tree in trees.items()}
    for _ in range(len(module_constants)):
        changed = False
        for path, tree in trees.items():
            constants = module_constants[_module_name(path)]
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in module_constants:
                    for alias in node.names:
                        imported = module_constants[node.module].get(alias.name)
                        local_name = alias.asname or alias.name
                        if imported is not None and constants.get(local_name) != imported:
                            constants[local_name] = imported
                            changed = True
        if not changed:
            break
    reads: set[str] = set()
    for path, tree in trees.items():
        imported_names: dict[str, object] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in module_constants:
                for alias in node.names:
                    if alias.name in module_constants[node.module]:
                        imported_names[alias.asname or alias.name] = module_constants[node.module][alias.name]
        reads.update(_scan_source((ROOT / path).read_text(encoding="utf-8"), path, imported_names))
    return reads


def _operator_doc_names() -> set[str]:
    names: set[str] = set()
    for line in OPERATOR_DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        first = line.split("|", 2)[1].strip().strip("`")
        if first and first not in {"Name", "---"} and not set(first) <= {"-", ":"}:
            names.add(first)
    return names


def _unregistered(reads: set[str], operator_names: set[str], internal: dict[str, str]):
    overlap = operator_names & internal.keys()
    unregistered = reads - operator_names - internal.keys()
    stale = (operator_names | internal.keys()) - reads
    return overlap, unregistered, stale


def _env_access_token_sites() -> list[tuple[str, int]]:
    sites: list[tuple[str, int]] = []
    for path in _production_python_paths():
        source = (ROOT / path).read_text(encoding="utf-8")
        for match in ENV_ACCESS_TOKEN.finditer(source):
            sites.append((path, source.count("\n", 0, match.start()) + 1))
    return sites


def _scratch_scan(
    tmp_path: Path,
    source: str,
    imported_names: dict[str, object] | None = None,
) -> set[str]:
    path = tmp_path / "scratch_module.py"
    path.write_text(source, encoding="utf-8")
    return _scan_source(path.read_text(encoding="utf-8"), str(path), imported_names)


def test_production_environment_reads_are_registered():
    reads = _production_env_reads()
    operator_names = _operator_doc_names()
    overlap, unregistered, stale = _unregistered(reads, operator_names, KNOWN_INTERNAL_ENV)
    assert not overlap, f"environment names are both operator and internal: {sorted(overlap)}"
    assert not unregistered, f"undocumented environment reads: {sorted(unregistered)}"
    assert not stale, f"stale environment registry entries: {sorted(stale)}"
    assert all(reason.strip() for reason in KNOWN_INTERNAL_ENV.values())


def test_environment_access_token_backstop():
    sites = _env_access_token_sites()
    assert len(sites) == ENV_ACCESS_TOKEN_CEILING, (
        "environ/getenv token count changed: register its key(s), review the "
        f"new count {len(sites)}, and update the ceiling with review; "
        f"ceiling={ENV_ACCESS_TOKEN_CEILING}, sites={sites})"
    )


def test_scanner_unions_branch_bindings_and_finds_fingerprint_reads(tmp_path):
    reads = _scratch_scan(tmp_path, '''
import os
def _runtime_environment_fingerprint(identity):
    if identity == "alphamelts":
        names = ("PATH", "DYLD_FALLBACK_LIBRARY_PATH", "LD_LIBRARY_PATH", "ALPHAMELTS_CALC_MODE")
    elif identity == "thermoengine":
        names = ("DYLD_FALLBACK_LIBRARY_PATH",)
    elif identity == "magemin":
        names = ("PATH",)
    selected = {name: os.environ.get(name) for name in names}
''')
    assert reads == {"PATH", "DYLD_FALLBACK_LIBRARY_PATH", "LD_LIBRARY_PATH", "ALPHAMELTS_CALC_MODE"}
    assert _unregistered(
        reads,
        _operator_doc_names() - {"LD_LIBRARY_PATH"},
        KNOWN_INTERNAL_ENV,
    )[1] == {"LD_LIBRARY_PATH"}


def test_scanner_sees_each_supported_environment_access_form(tmp_path):
    cases = (
        ('import os\nos.environ.pop("POP_KNOB", None)', {"POP_KNOB"}),
        ('import os as runtime\nruntime.getenv("ALIAS_KNOB")', {"ALIAS_KNOB"}),
        ('from os import getenv as read_env\nread_env("IMPORTED_GETENV")', {"IMPORTED_GETENV"}),
        ('from os import environ as env\nvalue = env["IMPORTED_ENVIRON"]', {"IMPORTED_ENVIRON"}),
        ('import os\nos.environ.get(key="KEYWORD_KNOB")', {"KEYWORD_KNOB"}),
        ('import os\nchild = os.environ.copy()\nchild.get("COPIED_ENV")', {"COPIED_ENV"}),
        ('import os\nchild = os.environ.copy()\nchild["COPIED_SUBSCRIPT"]', {"COPIED_SUBSCRIPT"}),
        ('import os\nos.environ.copy().get("CHAINED_COPY")', {"CHAINED_COPY"}),
        ('from os import environ as source\nchild = source.copy()\nchild.get("IMPORTED_COPY")', {"IMPORTED_COPY"}),
        ('import os\nenv = os.environ\nchild = env.copy()\nchild.get("ALIASED_COPY")', {"ALIASED_COPY"}),
    )
    for source, expected in cases:
        reads = _scratch_scan(tmp_path, source)
        assert reads == expected
        assert _unregistered(reads, set(), {})[1] == expected


def test_token_backstop_catches_ast_forms_outside_key_scanner(tmp_path):
    source = 'import os\nvalue = getattr(os.environ, "get")( "BACKSTOP_ONLY" )\n'
    path = tmp_path / "exotic.py"
    path.write_text(source, encoding="utf-8")
    assert len(ENV_ACCESS_TOKEN.findall(path.read_text(encoding="utf-8"))) == 1


def test_dict_environ_access_is_a_documented_scanner_limitation():
    source = 'import os\nvalue = dict(os.environ)["DICT_ONLY"]\n'
    assert _scan_source(source) == set()
    assert len(ENV_ACCESS_TOKEN.findall(source)) == 1


def test_scanner_refuses_unresolved_local_and_cross_module_keys(tmp_path):
    for source in (
        "import os\nos.environ.get(dynamic_name)",
        'from other_module import name\nimport os\nos.environ.get(name)\n',
        "import os\nos.environ.pop(dynamic_name, None)\n",
    ):
        try:
            imported_names = {"name": ""} if "other_module" in source else None
            _scratch_scan(tmp_path, source, imported_names)
        except AssertionError as exc:
            assert "scratch_module.py:" in str(exc)
            assert "unresolved environment key" in str(exc)
        else:
            raise AssertionError("unresolved environment key escaped the scan")


def test_wrapper_does_not_drop_later_dynamic_key_call(tmp_path):
    source = '''
import os
def read(key):
    return os.environ.get(key)
read("SEEN")
read(dynamic_name)
'''
    try:
        _scratch_scan(tmp_path, source)
    except AssertionError as exc:
        assert "unresolved environment key argument 'key'" in str(exc)
    else:
        raise AssertionError("dynamic helper key escaped the scan")


def test_registry_rejects_added_stale_and_conflicting_names():
    assert _unregistered({"LIVE"}, {"LIVE"}, {}) == (set(), set(), set())
    assert _unregistered({"LIVE", "NEW"}, {"LIVE"}, {})[1] == {"NEW"}
    assert _unregistered({"LIVE"}, set(), {"OLD": "diagnostic"})[2] == {"OLD"}
    assert _unregistered({"LIVE"}, {"LIVE"}, {"LIVE": "debug"})[0] == {"LIVE"}
