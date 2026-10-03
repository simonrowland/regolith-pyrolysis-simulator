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


def _imports(tree: ast.AST, source: str, package: str, modules: set[str]):
    found: list[tuple[str, int]] = []

    def add(node: ast.AST, base: str, names: list[ast.alias] | None = None) -> None:
        if base in modules:
            found.append((base, node.lineno))
        for alias in names or ():
            child = f"{base}.{alias.name}" if base else alias.name
            if child in modules:
                found.append((child, node.lineno))

    class Visitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            if _is_type_checking(node.test):
                for statement in node.orelse:
                    self.visit(statement)
                return
            self.generic_visit(node)

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

    Visitor().visit(tree)
    return found


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


def _scan(files: dict[str, tuple[str, str]]) -> tuple[dict[tuple[str, str], list[int]], set[str]]:
    """Return module edges with lines and modules whose layer is undefined."""
    modules = set(files)
    layers = _layer_prefixes()

    def layer_for(module: str) -> int | None:
        matches = [prefix for prefix in layers if module == prefix or module.startswith(prefix + ".")]
        if not matches:
            return None
        return layers[max(matches, key=len)]

    edges: dict[tuple[str, str], set[int]] = {}
    for source, (path, text) in files.items():
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            raise AssertionError(f"cannot parse {path}: {exc}") from exc
        package = source if Path(path).name == "__init__.py" else source.rpartition(".")[0]
        for target, line in _imports(tree, source, package, modules):
            edges.setdefault((source, target), set()).add(line)
    unmatched = {module for module in modules if layer_for(module) is None}
    upward = {
        edge: sorted(lines)
        for edge, lines in edges.items()
        if layer_for(edge[0]) is not None
        and layer_for(edge[1]) is not None
        and layer_for(edge[0]) < layer_for(edge[1])
    }
    return upward, unmatched


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
        errors.append(f"STALE baseline edge {source} -> {target}: delete it")
    return errors


def test_import_boundary_baseline_is_shrink_only() -> None:
    files = _project_files(ROOT)
    upward, unmatched = _scan(files)
    baseline_data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline = {(row["source"], row["target"]) for row in baseline_data}
    errors = _check_baseline(upward, baseline, {module: path for module, (path, _) in files.items()})
    errors.extend(f"module matches no layer: {module}" for module in sorted(unmatched))
    assert not errors, "\n".join(errors)


def test_new_upward_edge_fails_with_caller_remediation(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text("import simulator.high\n")
    high = path.parent / "high.py"
    high.write_text("VALUE = 1\n")
    edges, unmatched = _scan({
        "simulator.chemistry.low": (str(path), path.read_text()),
        "simulator.high": (str(high), high.read_text()),
    })
    assert not unmatched
    errors = _check_baseline(edges, set(), {"simulator.chemistry.low": str(path)})
    assert len(errors) == 1
    assert "simulator.chemistry.low -> simulator.high" in errors[0]
    assert f"{path}:1" in errors[0]
    assert REMEDIATION in errors[0]


def test_stale_baseline_edge_fails(tmp_path: Path) -> None:
    path = tmp_path / "simulator" / "low.py"
    path.parent.mkdir()
    path.write_text("VALUE = 1\n")
    edges, unmatched = _scan({"simulator.low": (str(path), path.read_text())})
    assert not edges and not unmatched
    errors = _check_baseline(edges, {("simulator.low", "simulator.high")})
    assert errors == ["STALE baseline edge simulator.low -> simulator.high: delete it"]
