"""Shrink-only import-layer guard for tracked Python modules."""
from __future__ import annotations

import ast
from collections import Counter
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


def _visit_runtime_if(visitor: ast.NodeVisitor, node: ast.If) -> bool:
    if not _is_type_checking(node.test):
        return False
    for statement in node.orelse:
        visitor.visit(statement)
    return True


def _imports(tree: ast.AST, source: str, path: str, package: str, modules: set[str]):
    found: list[tuple[str, int]] = []
    dynamic_sites: Counter[str] = Counter()

    def add(node: ast.AST, base: str, names: list[ast.alias] | None = None) -> None:
        if base in modules:
            found.append((base, node.lineno))
        for alias in names or ():
            child = f"{base}.{alias.name}" if base else alias.name
            if child in modules:
                found.append((child, node.lineno))

    class AliasCollector(ast.NodeVisitor):
        """Collect dynamic-import spellings before resolving references in source order."""

        def __init__(self) -> None:
            self.importlib_names = {"importlib"}
            self.import_module_names = {"import_module"}

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if alias.name == "importlib":
                    self.importlib_names.add(alias.asname or "importlib")

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module == "importlib":
                for alias in node.names:
                    if alias.name == "import_module":
                        self.import_module_names.add(alias.asname or alias.name)

        def visit_If(self, node: ast.If) -> None:
            if not _visit_runtime_if(self, node):
                self.generic_visit(node)

    aliases = AliasCollector()
    aliases.visit(tree)

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope_stack: list[str] = []
            self.importlib_names = aliases.importlib_names
            self.import_module_names = aliases.import_module_names

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                add(node, alias.name)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
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

        def _is_dynamic_reference(self, node: ast.AST) -> bool:
            return (
                isinstance(node, ast.Name)
                and (node.id == "__import__" or node.id in self.import_module_names)
            ) or (
                isinstance(node, ast.Attribute)
                and node.attr == "import_module"
                and isinstance(node.value, ast.Name)
                and node.value.id in self.importlib_names
            )

        def _record_computed(self) -> None:
            function = ".".join(self.scope_stack) or "<module>"
            dynamic_sites[f"{path}:{function}"] += 1

        @staticmethod
        def _literal_value(node: ast.AST) -> tuple[bool, object]:
            try:
                return True, ast.literal_eval(node)
            except (ValueError, TypeError):
                return False, None

        def _resolved_dynamic_arguments(
            self, node: ast.Call,
        ) -> tuple[str, str, str | None, tuple[str, ...]] | None:
            """Resolve only direct calls matching the supported literal shapes.

            ``import_module`` accepts a literal name (positional or ``name=``),
            optionally with ``package=<str literal>``. ``__import__`` accepts a
            literal name, absent or zero literal level, and an absent, ``None``,
            or all-string literal list/tuple fromlist; globals and locals may be
            arbitrary. Starred args, ``**kwargs``, duplicate or unknown keywords,
            extra arguments, and every other argument shape are computed.
            """
            if any(isinstance(argument, ast.Starred) for argument in node.args):
                return None
            is_dunder = isinstance(node.func, ast.Name) and node.func.id == "__import__"
            if not is_dunder:
                parameters = ("name", "package")
                if len(node.args) > 1:
                    return None
                arguments = {"name": node.args[0]} if node.args else {}
                for keyword in node.keywords:
                    if keyword.arg not in parameters or keyword.arg in arguments:
                        return None
                    arguments[keyword.arg] = keyword.value
                name_node = arguments.get("name")
                name_is_literal, name = self._literal_value(name_node) if name_node is not None else (False, None)
                package_node = arguments.get("package")
                package_is_literal, package = (
                    self._literal_value(package_node) if package_node is not None else (True, None)
                )
                if not name_is_literal or not isinstance(name, str):
                    return None
                if not package_is_literal or (
                    package_node is not None and not isinstance(package, str)
                ):
                    return None
                return "import_module", name, package, ()

            parameters = ("name", "globals", "locals", "fromlist", "level")
            if len(node.args) > len(parameters):
                return None
            arguments = dict(zip(parameters, node.args))
            for keyword in node.keywords:
                if keyword.arg not in parameters or keyword.arg in arguments:
                    return None
                arguments[keyword.arg] = keyword.value
            name_node = arguments.get("name")
            name_is_literal, name = self._literal_value(name_node) if name_node is not None else (False, None)
            if not name_is_literal or not isinstance(name, str):
                return None
            level_node = arguments.get("level")
            if level_node is not None:
                level_is_literal, level = self._literal_value(level_node)
                if not level_is_literal or type(level) is not int or level != 0:
                    return None
            fromlist_node = arguments.get("fromlist")
            if fromlist_node is None:
                fromlist: tuple[str, ...] = ()
            else:
                fromlist_is_literal, value = self._literal_value(fromlist_node)
                if value is None and fromlist_is_literal:
                    fromlist = ()
                elif (
                    fromlist_is_literal
                    and isinstance(value, (list, tuple))
                    and all(isinstance(item, str) for item in value)
                ):
                    fromlist = tuple(value)
                else:
                    return None
            return "__import__", name, None, fromlist

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
            if self._is_dynamic_reference(node.func):
                resolved = self._resolved_dynamic_arguments(node)
                target = None
                if resolved is not None:
                    kind, literal_name, literal_package, fromlist = resolved
                    if not literal_name.startswith("."):
                        target = literal_name
                    elif kind == "import_module" and literal_package is not None:
                        dots = len(literal_name) - len(literal_name.lstrip("."))
                        parts = literal_package.split(".")
                        if dots > len(parts):
                            raise AssertionError(f"invalid relative dynamic import in {path}:{node.lineno}")
                        target = ".".join((*parts[:len(parts) - dots + 1], literal_name[dots:])).rstrip(".")
                if target is not None:
                    add(node, target)
                    if kind == "__import__" and fromlist:
                        add(node, target, [ast.alias(name=name, asname=None) for name in fromlist])
                else:
                    self._record_computed()
                # A recognized call accounts for its function reference once.
                for argument in node.args:
                    self.visit(argument)
                for keyword in node.keywords:
                    self.visit(keyword.value)
                return
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            if self._is_dynamic_reference(node):
                self._record_computed()

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if self._is_dynamic_reference(node):
                self._record_computed()
                return
            self.generic_visit(node)

        def visit_If(self, node: ast.If) -> None:
            if not _visit_runtime_if(self, node):
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


def _scan(files: dict[str, tuple[str, str]]) -> tuple[dict[tuple[str, str], list[int]], set[str], dict[str, int]]:
    """Return module edges, undefined-layer modules, and computed dynamic-import sites."""
    modules = set(files)
    layers = _layer_prefixes()

    def layer_for(module: str) -> int | None:
        matches = [prefix for prefix in layers if module == prefix or module.startswith(prefix + ".")]
        if not matches:
            return None
        return layers[max(matches, key=len)]

    edges: dict[tuple[str, str], set[int]] = {}
    dynamic_sites: Counter[str] = Counter()
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


def _check_dynamic_sites(sites: dict[str, int], baseline: dict[str, int]) -> list[str]:
    errors = []
    for site in sorted(sites.keys() | baseline.keys()):
        current = sites.get(site, 0)
        allowed = baseline.get(site, 0)
        if current > allowed:
            errors.append(
                f"NEW computed dynamic import at {site}: {current} exceeds baseline {allowed}: {DYNAMIC_REMEDIATION}"
            )
        elif current < allowed:
            errors.append(
                f"STALE dynamic import site {site}: count dropped from {allowed} to {current}; lower the count / delete the entry in the same commit that removes the call"
            )
    return errors


def test_import_boundary_baseline_is_shrink_only() -> None:
    files = _project_files(ROOT)
    upward, unmatched, dynamic_sites = _scan(files)
    baseline_data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline = {(row["source"], row["target"]) for row in baseline_data["edges"]}
    baseline_sites = baseline_data["dynamic_import_sites"]
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


def test_keyword_literal_importlib_import_is_an_edge(tmp_path: Path) -> None:
    _assert_literal_dynamic_edge(
        tmp_path,
        'def load():\n    return il.import_module(name="simulator.high")\nimport importlib as il\n',
    )


def test_dunder_import_fromlist_adds_tracked_child_edge(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text('def load():\n    return __import__("simulator", fromlist=["high"])\n')
    high = path.parent / "high.py"
    high.write_text("VALUE = 1\n")
    edges, unmatched, sites = _scan({
        "simulator.chemistry.low": (str(path), path.read_text()),
        "simulator": (str(path.parent / "__init__.py"), ""),
        "simulator.high": (str(high), high.read_text()),
    })
    assert not unmatched and not sites
    errors = _check_baseline(edges, set(), {"simulator.chemistry.low": str(path)})
    assert any("simulator.chemistry.low -> simulator.high" in error for error in errors)


def test_unsupported_dynamic_import_shapes_are_counted(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    cases = {
        "relative_import": '__import__("high", level=1)',
        "set_fromlist": '__import__("simulator", fromlist={"high"})',
        "dict_fromlist": '__import__("simulator", fromlist={"high": None})',
        "kwargs_call": "__import__(**options)",
    }
    path.write_text("\n".join(
        f"def {name}():\n    return {call}" for name, call in cases.items()
    ) + "\n")
    _, _, sites = _scan({"simulator.chemistry.low": (str(path), path.read_text())})
    assert sites == {f"{path}:{name}": 1 for name in cases}


def test_dynamic_import_alias_reference_is_counted(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text("import importlib as il\nf = il.import_module\n")
    _, _, sites = _scan({"simulator.chemistry.low": (str(path), path.read_text())})
    assert sites == {f"{path}:<module>": 1}
    assert _check_dynamic_sites(sites, {})


def test_second_computed_dynamic_reference_in_function_fails(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text(
        "def load(name):\n    __import__(name)\n    __import__(name)\n"
    )
    _, _, sites = _scan({"simulator.chemistry.low": (str(path), path.read_text())})
    assert sites == {f"{path}:load": 2}
    assert _check_dynamic_sites(sites, {f"{path}:load": 1})


def test_partial_dynamic_import_removal_fails_as_stale(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text("def load(name):\n    __import__(name)\n")
    _, _, sites = _scan({"simulator.chemistry.low": (str(path), path.read_text())})
    assert _check_dynamic_sites(sites, {f"{path}:load": 2}) == [
        f"STALE dynamic import site {path}:load: count dropped from 2 to 1; lower the count / delete the entry in the same commit that removes the call"
    ]


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
    assert _check_dynamic_sites(sites, {}) == [
        f"NEW computed dynamic import at {path}:load: 1 exceeds baseline 0: {DYNAMIC_REMEDIATION}"
    ]


def test_stale_dynamic_import_site_fails(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text("VALUE = 1\n")
    _, _, sites = _scan({"simulator.chemistry.low": (str(path), path.read_text())})
    assert _check_dynamic_sites(sites, {f"{path}:load": 1}) == [
        f"STALE dynamic import site {path}:load: count dropped from 1 to 0; lower the count / delete the entry in the same commit that removes the call"
    ]
