"""Keep production environment reads documented or explicitly internal."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = ("simulator/", "engines/", "web/", "tools/", "scripts/")
PRODUCTION_FILES = ("app.py",)
OPERATOR_DOC = ROOT / "docs" / "configuration.md"

# Internal means a probe, diagnostic, debug switch, or harness control. Keep
# each reason short and specific: these names are intentionally not operator
# configuration for normal simulator or web use.
KNOWN_INTERNAL_ENV = {
    "FIDELITY_DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH": "Enables internal analytical-model comparison in the fidelity diagnostic harness.",
    "FIDELITY_DIAGNOSTIC_STUB_HIGH": "Legacy alias used only by the fidelity diagnostic harness.",
    "FIDELITY_HIGH_BACKEND": "Selects a backend for the fidelity DOE harness.",
    "FIDELITY_HIGH_HOURS": "Sets run duration in the fidelity DOE harness.",
    "FIDELITY_SKIP_PROFILE_CONSTRAINTS": "Disables profile constraints in the fidelity DOE harness.",
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
    "XDG_CACHE_HOME": "Selects the XDG cache root for an internal profiling script.",
}


def _static_value(node: ast.AST, names: dict[str, object]) -> object | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_static_value(item, names) for item in node.elts]
        if any(value is None for value in values):
            return None
        return tuple(values)
    return None


def _is_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _scan_source(source: str, shared_names: dict[str, object] | None = None) -> set[str]:
    tree = ast.parse(source)
    names: dict[str, object] = dict(shared_names or {})
    function_parameters: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_parameters[node.name] = [arg.arg for arg in node.args.args]
    call_values: dict[tuple[str, int], list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        for index, arg in enumerate(node.args):
            value = _static_value(arg, names)
            if isinstance(value, str):
                call_values.setdefault((node.func.id, index), []).append(value)
    for function, parameters in function_parameters.items():
        for index, parameter in enumerate(parameters):
            values = call_values.get((function, index), [])
            if values:
                names[parameter] = tuple(values)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value_node = node.value
            if value_node is None:
                continue
            value = _static_value(value_node, names)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    names[target.id] = value

    # Resolve keys bound by for-loops and generator expressions, such as
    # os.environ.get(name) where name ranges over a literal tuple.
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.comprehension)):
            value = _static_value(node.iter, names)
            if isinstance(node.target, ast.Name) and isinstance(value, tuple):
                names[node.target.id] = value

    reads: set[str] = set()
    for node in ast.walk(tree):
        key_node: ast.AST | None = None
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            key_node = node.slice
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            if _is_environ(func.value) and func.attr in {"get", "setdefault"}:
                key_node = node.args[0] if node.args else None
            elif (
                isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr == "getenv"
            ):
                key_node = node.args[0] if node.args else None
            elif (
                isinstance(func.value, ast.Name)
                and func.value.id == "environ"
                and func.attr == "get"
            ):
                key_node = node.args[0] if node.args else None
        elif isinstance(node, ast.Compare) and len(node.ops) == 1:
            if any(_is_environ(comp) for comp in node.comparators):
                key_node = node.left

        value = _static_value(key_node, names) if key_node is not None else None
        if key_node is not None and value is None:
            raise AssertionError(
                f"could not resolve environment key at line {node.lineno}: "
                f"{ast.unparse(key_node)}"
            )
        if isinstance(value, str):
            if value:
                reads.add(value)
        elif isinstance(value, tuple):
            reads.update(item for item in value if isinstance(item, str) and item)
    return reads


def _production_env_reads() -> set[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.split(b"\0")
    paths = [
        ROOT / raw.decode()
        for raw in tracked
        if raw
        and raw.decode().endswith(".py")
        and (
            raw.decode() in PRODUCTION_FILES
            or raw.decode().startswith(PRODUCTION_ROOTS)
        )
    ]
    sources = [path.read_text(encoding="utf-8") for path in paths]
    shared_names: dict[str, object] = {}
    for source in sources:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value_node = node.value
                if value_node is None:
                    continue
                value = _static_value(value_node, shared_names)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and value is not None:
                        shared_names[target.id] = value
    reads: set[str] = set()
    for source in sources:
        reads.update(_scan_source(source, shared_names))
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


def test_production_environment_reads_are_registered():
    reads = _production_env_reads()
    operator_names = _operator_doc_names()
    overlap, unregistered, stale = _unregistered(
        reads, operator_names, KNOWN_INTERNAL_ENV
    )
    assert not overlap, f"environment names are both operator and internal: {sorted(overlap)}"
    assert not unregistered, f"undocumented environment reads: {sorted(unregistered)}"
    assert not stale, f"stale environment registry entries: {sorted(stale)}"
    assert all(reason.strip() for reason in KNOWN_INTERNAL_ENV.values())


def test_scanner_detects_new_literal_environment_reads():
    assert _scan_source('import os\nos.environ.get("NEW_KNOB", "fallback")') == {
        "NEW_KNOB"
    }


def test_scanner_resolves_constant_and_loop_environment_keys():
    source = '''
import os
FIRST = "FIRST_KNOB"
KEYS = ("LOOP_A", "LOOP_B")
os.environ.get(FIRST)
for key in KEYS:
    os.environ.get(key)
'''
    assert _scan_source(source) == {"FIRST_KNOB", "LOOP_A", "LOOP_B"}


def test_scanner_refuses_unresolved_dynamic_environment_keys():
    try:
        _scan_source("import os\nos.environ.get(dynamic_name)")
    except AssertionError as exc:
        assert "dynamic_name" in str(exc)
    else:
        raise AssertionError("unresolved environment key escaped the scan")


def test_registry_rejects_stale_names_and_conflicting_classification():
    assert _unregistered({"LIVE"}, {"LIVE"}, {}) == (set(), set(), set())
    assert _unregistered({"LIVE", "NEW"}, {"LIVE"}, {})[1] == {"NEW"}
    assert _unregistered({"LIVE"}, set(), {"OLD": "diagnostic"})[2] == {"OLD"}
    assert _unregistered({"LIVE"}, {"LIVE"}, {"LIVE": "debug"})[0] == {"LIVE"}
