"""Shrink-only import-layer guard for tracked Python modules."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ("simulator/", "engines/", "web/", "tools/", "scripts/")
LAYERS_PATH = Path(__file__).with_name("import_layers.toml")
BASELINE_PATH = Path(__file__).with_name("import_boundary_baseline.json")
REMEDIATION = "move the dependency to the caller; never add a baseline entry"
DYNAMIC_REMEDIATION = "make it a literal or a static import"


def _module_for(path: str) -> str:
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    )


def _imports(tree: ast.AST, source: str, path: str, package: str, modules: set[str]):
    found: list[tuple[str, int]] = []
    dynamic_sites: set[str] = set()

    def add(node: ast.AST, base: str, names: list[ast.alias] | None = None) -> None:
        if base in modules:
            found.append((base, node.lineno))
        for alias in names or ():
            child = f"{base}.{alias.name}" if base else alias.name
            if child in modules:
                found.append((child, node.lineno))

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope_stack: list[str] = []
            self.importlib_names = {"importlib"}
            self.import_module_names = {"import_module"}

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if alias.name == "importlib":
                    self.importlib_names.add(alias.asname or "importlib")
            for alias in node.names:
                add(node, alias.name)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module == "importlib":
                for alias in node.names:
                    if alias.name == "import_module":
                        self.import_module_names.add(alias.asname or alias.name)
            self._visit_import_from(node)

        def _visit_import_from(self, node: ast.ImportFrom) -> None:
            if node.level:
                parts = package.split(".") if package else []
                trim = node.level - 1
                if trim:
                    parts = parts[:-trim]
                base = ".".join(parts)
                if node.module:
                    base = ".".join(part for part in (base, node.module) if part)
            else:
                base = node.module or ""
            if base:
                add(node, base, node.names)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.scope_stack.append(node.name)
            self.generic_visit(node)
            self.scope_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scope_stack.append(node.name)
            self.generic_visit(node)
            self.scope_stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            dynamic = False
            if isinstance(node.func, ast.Name) and node.func.id in self.import_module_names:
                dynamic = True
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.importlib_names
            ):
                dynamic = True
            elif isinstance(node.func, ast.Name) and node.func.id == "__import__":
                dynamic = True
            if dynamic:
                name = node.args[0] if node.args else None
                literal_name = name.value if isinstance(name, ast.Constant) and isinstance(name.value, str) else None
                package_arg = next((kw.value for kw in node.keywords if kw.arg == "package"), None)
                literal_package = (
                    package_arg.value
                    if isinstance(package_arg, ast.Constant) and isinstance(package_arg.value, str)
                    else None
                )
                target = None
                if literal_name is not None and not literal_name.startswith("."):
                    target = literal_name
                elif literal_name is not None and literal_package is not None:
                    dots = len(literal_name) - len(literal_name.lstrip("."))
                    parts = literal_package.split(".")
                    if dots > len(parts):
                        raise AssertionError(f"invalid relative dynamic import in {path}:{node.lineno}")
                    target = ".".join((*parts[:len(parts) - dots + 1], literal_name[dots:])).rstrip(".")
                if target:
                    add(node, target)
                else:
                    function = ".".join(self.scope_stack) or "<module>"
                    dynamic_sites.add(f"{path}:{function}")
            self.generic_visit(node)

        def visit_If(self, node: ast.If) -> None:
            if _is_type_checking(node.test):
                for statement in node.orelse:
                    self.visit(statement)
                return
            self.generic_visit(node)

    Visitor().visit(tree)
    return found, dynamic_sites


def _tracked_python_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *SCOPE], cwd=root,
        check=True, capture_output=True,
    )
    return sorted(p for p in result.stdout.decode().split("\0") if p.endswith(".py"))


def _layer_prefixes() -> dict[str, int]:
    prefixes: dict[str, int] = {}
    in_layers = False
    for line in LAYERS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("["):
            in_layers = line == "[layers]"
        elif in_layers and line and not line.startswith("#"):
            key, separator, value = line.partition("=")
            if separator:
                prefixes[json.loads(key.strip())] = int(value.strip())
    return prefixes


def _scan(files: dict[str, tuple[str, str]]) -> tuple[dict[tuple[str, str], list[int]], set[str], set[str]]:
    """Return module edges, undefined-layer modules, and computed dynamic-import sites."""
    modules = set(files)
    layers = _layer_prefixes()

    def layer_for(module: str) -> int | None:
        matches = [prefix for prefix in layers if module == prefix or module.startswith(prefix + ".")]
        if not matches:
            return None
        return layers[max(matches, key=len)]

    edges: dict[tuple[str, str], set[int]] = {}
    dynamic_sites: set[str] = set()
    for source, (path, text) in files.items():
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            raise AssertionError(f"cannot parse {path}: {exc}") from exc
        package = source if Path(path).name == "__init__.py" else source.rpartition(".")[0]
        imports, sites = _imports(tree, source, path, package, modules)
        dynamic_sites.update(sites)
        for target, line in imports:
            edges.setdefault((source, target), set()).add(line)
    unmatched = {module for module in modules if layer_for(module) is None}
    upward = {
        edge: sorted(lines)
        for edge, lines in edges.items()
        if layer_for(edge[0]) is not None
        and layer_for(edge[1]) is not None
        and layer_for(edge[0]) < layer_for(edge[1])
    }
    return upward, unmatched, dynamic_sites


def _project_files(root: Path) -> dict[str, tuple[str, str]]:
    files: dict[str, tuple[str, str]] = {}
    for path in _tracked_python_files(root):
        module = _module_for(path)
        files[module] = (path, (root / path).read_text(encoding="utf-8"))
    return files


def _check_baseline(
    edges: dict[tuple[str, str], list[int]], baseline: set[tuple[str, str]],
    source_paths: dict[str, str] | None = None,
) -> list[str]:
    errors = []
    for source, target in sorted(edges.keys() - baseline):
        path = (source_paths or {}).get(source, source.replace(".", "/") + ".py")
        errors.append(
            f"{source} -> {target} at {path}:{','.join(map(str, edges[(source, target)]))}: {REMEDIATION}"
        )
    for source, target in sorted(baseline - edges.keys()):
        errors.append(f"STALE baseline edge {source} -> {target}: delete it in the same commit that removes the import")
    return errors


def _check_dynamic_sites(sites: set[str], baseline: set[str]) -> list[str]:
    errors = [f"NEW computed dynamic import at {site}: {DYNAMIC_REMEDIATION}" for site in sorted(sites - baseline)]
    errors.extend(f"STALE dynamic import site {site}: delete it" for site in sorted(baseline - sites))
    return errors


def test_import_boundary_baseline_is_shrink_only() -> None:
    files = _project_files(ROOT)
    upward, unmatched, dynamic_sites = _scan(files)
    baseline_data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline = {(row["source"], row["target"]) for row in baseline_data["edges"]}
    baseline_sites = set(baseline_data["dynamic_import_sites"])
    errors = _check_baseline(upward, baseline, {module: path for module, (path, _) in files.items()})
    errors.extend(_check_dynamic_sites(dynamic_sites, baseline_sites))
    errors.extend(f"module matches no layer: {module}" for module in sorted(unmatched))
    assert not errors, "\n".join(errors)


def test_new_upward_edge_fails_with_caller_remediation(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text("import simulator.high\n")
    high = path.parent / "high.py"
    high.write_text("VALUE = 1\n")
    edges, unmatched, sites = _scan({
        "simulator.chemistry.low": (str(path), path.read_text()),
        "simulator.high": (str(high), high.read_text()),
    })
    assert not unmatched and not sites
    errors = _check_baseline(edges, set(), {"simulator.chemistry.low": str(path)})
    assert len(errors) == 1
    assert "simulator.chemistry.low -> simulator.high" in errors[0]
    assert f"{path}:1" in errors[0]
    assert REMEDIATION in errors[0]


def test_stale_baseline_edge_fails(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text("VALUE = 1\n")
    edges, unmatched, sites = _scan({"simulator.low": (str(path), path.read_text())})
    assert not edges and not unmatched and not sites
    errors = _check_baseline(edges, {("simulator.low", "simulator.high")})
    assert errors == ["STALE baseline edge simulator.low -> simulator.high: delete it in the same commit that removes the import"]


def test_literal_importlib_import_is_an_edge(tmp_path: Path) -> None:
    _assert_literal_dynamic_edge(tmp_path, 'import importlib\ndef load():\n    return importlib.import_module("simulator.high")\n')


def test_literal_importlib_relative_import_is_an_edge(tmp_path: Path) -> None:
    _assert_literal_dynamic_edge(
        tmp_path,
        'from importlib import import_module as load\ndef run():\n    return load(".high", package="simulator")\n',
    )


def test_literal_dunder_import_is_an_edge(tmp_path: Path) -> None:
    _assert_literal_dynamic_edge(tmp_path, 'def load():\n    return __import__("simulator.high")\n')


def _assert_literal_dynamic_edge(tmp_path: Path, source: str) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text(source)
    high = path.parent / "high.py"
    high.write_text("VALUE = 1\n")
    edges, unmatched, sites = _scan({
        "simulator.chemistry.low": (str(path), path.read_text()),
        "simulator.high": (str(high), high.read_text()),
    })
    assert not unmatched and not sites
    errors = _check_baseline(edges, set(), {"simulator.chemistry.low": str(path)})
    assert len(errors) == 1
    assert "simulator.chemistry.low -> simulator.high" in errors[0]
    assert REMEDIATION in errors[0]


def test_new_computed_dynamic_import_site_fails(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text("def load(name):\n    return __import__(name)\n")
    _, _, sites = _scan({"simulator.chemistry.low": (str(path), path.read_text())})
    assert _check_dynamic_sites(sites, set()) == [
        f"NEW computed dynamic import at {path}:load: {DYNAMIC_REMEDIATION}"
    ]


def test_stale_dynamic_import_site_fails(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text("VALUE = 1\n")
    _, _, sites = _scan({"simulator.chemistry.low": (str(path), path.read_text())})
    assert _check_dynamic_sites(sites, {f"{path}:load"}) == [
        f"STALE dynamic import site {path}:load: delete it"
    ]
