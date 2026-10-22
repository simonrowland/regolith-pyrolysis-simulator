"""Derive private test inputs from static readers, manifests, and file globs.

The static scan recognizes path assignments, direct pathlib reads, and literal
glob patterns. It cannot resolve every helper return, f-string, or function-local
path join; strict CI also audits attempted opens so those misses fail loudly.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROOT_NAMES = {"ROOT", "REPO_ROOT", "_REPO_ROOT"}
MANIFEST_NAME = re.compile(r"manifest-c6-[a-z0-9-]+\.json\Z")
PATH_READ_METHODS = {
    "exists",
    "is_file",
    "is_dir",
    "open",
    "read_bytes",
    "read_text",
    "stat",
}


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.List, ast.Tuple)):
        return [name for item in target.elts for name in _target_names(item)]
    return []


def _assignments(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [name for target in node.targets for name in _target_names(target)]
            yield names, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            yield _target_names(node.target), node.value


def _resolve_path(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in aliases:
            return aliases[node.id]
        return "" if node.id in ROOT_NAMES else None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _resolve_path(node.left, aliases)
        right = _resolve_path(node.right, aliases)
        if left is None:
            return right
        if right is None:
            return left
        return (PurePosixPath(left) / right).as_posix()
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return next(
            (
                path
                for value in reversed(node.values)
                if (path := _resolve_path(value, aliases)) is not None
            ),
            None,
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Path"
        and node.args
    ):
        return _resolve_path(node.args[0], aliases)
    return None


def _private_path(path: str | None) -> str | None:
    if path is None:
        return None
    parts = PurePosixPath(path).parts
    try:
        index = parts.index("docs-private")
    except ValueError:
        return None
    return PurePosixPath(*parts[index:]).as_posix()


def _source_files(root: Path) -> list[Path]:
    files = [root / "conftest.py"]
    # This file uses synthetic docs-private paths to exercise the preflight.
    files.extend(
        path
        for path in sorted((root / "tests").rglob("*.py"))
        if path.relative_to(root).as_posix() != "tests/test_docs_private_preflight.py"
    )
    files.extend(
        root / path
        for path in (
            "simulator/chemistry/phase_context.py",
            "simulator/vapour_rail/u0_manifest.py",
        )
    )
    return [path for path in files if path.is_file()]


def _path_names_read_by_tests(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in PATH_READ_METHODS
            and isinstance(node.func.value, ast.Name)
        ):
            names.add(node.func.value.id)
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in PATH_READ_METHODS
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "Path"
            and node.func.value.args
            and isinstance(node.func.value.args[0], ast.Name)
        ):
            names.add(node.func.value.args[0].id)
        elif isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
            if isinstance(node.args[0], ast.Name):
                names.add(node.args[0].id)
    return names


def _readers(
    root: Path,
) -> tuple[dict[str, set[str]], dict[Path, ast.AST], dict[Path, dict[str, str]]]:
    paths: dict[str, set[str]] = {}
    trees: dict[Path, ast.AST] = {}
    aliases_by_file: dict[Path, dict[str, str]] = {}
    for source in _source_files(root):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        aliases: dict[str, str] = {}
        is_test = source.is_relative_to(root / "tests")
        read_names = _path_names_read_by_tests(tree) if is_test else set()
        paths_by_name: dict[str, set[str]] = {}
        collection_names: set[str] = set()
        trees[source] = tree
        aliases_by_file[source] = aliases

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [name for target in node.targets for name in _target_names(target)]
                values = [node.value]
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    collection_names.update(names)
                    values = list(node.value.elts)
                for value in values:
                    if any(
                        isinstance(child, ast.Name)
                        and child.id in {"tmp_path", "tmpdir"}
                        for child in ast.walk(value)
                    ):
                        continue
                    path = _private_path(_resolve_path(value, aliases))
                    if path is None:
                        continue
                    if (
                        is_test
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                        and not any(
                            name.endswith("SUBPATH") or name in read_names
                            for name in names
                        )
                    ):
                        # Raw strings used as provenance labels are not file inputs.
                        continue
                    reader = source.relative_to(root).as_posix()
                    for name in names:
                        paths.setdefault(path, set()).add(reader)
                        paths_by_name.setdefault(name, set()).add(path)
                        if isinstance(node, ast.Assign) and node in tree.body:
                            aliases[name] = path
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                names = _target_names(node.target)
                path = _private_path(_resolve_path(node.value, aliases))
                if path is not None:
                    reader = source.relative_to(root).as_posix()
                    for name in names:
                        paths.setdefault(path, set()).add(reader)
                        paths_by_name.setdefault(name, set()).add(path)
                        if node in tree.body:
                            aliases[name] = path

        for node in ast.walk(tree):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            name_paths = paths_by_name.get(node.id, set())
            if len(name_paths) == 1 or node.id in collection_names:
                for path in name_paths:
                    paths[path].add(
                        source.relative_to(root).as_posix()
                    )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in PATH_READ_METHODS:
                target = node.func.value
            elif isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
                target = node.args[0]
            else:
                continue
            if any(
                isinstance(child, ast.Name)
                and child.id in {"tmp_path", "tmpdir"}
                for child in ast.walk(target)
            ):
                continue
            path = _private_path(_resolve_path(target, aliases))
            if path is not None:
                paths.setdefault(path, set()).add(
                    source.relative_to(root).as_posix()
                )

        # The corpus root is a module-level path string consumed by the glob
        # reader; it is not itself a file to copy.
        for names, value in _assignments(tree):
            if not any(name.endswith("SUBPATH") for name in names):
                continue
            path = _private_path(_resolve_path(value, aliases))
            if path is not None:
                paths.setdefault(path, set()).add(
                    source.relative_to(root).as_posix()
                )
                for name in names:
                    aliases[name] = path
    return paths, trees, aliases_by_file


def _glob_specs(
    root: Path,
    trees: dict[Path, ast.AST],
    aliases_by_file: dict[Path, dict[str, str]],
) -> list[tuple[str, Path]]:
    specs = []
    for source, tree in trees.items():
        aliases = aliases_by_file[source]
        subpaths = [
            path
            for name, path in aliases.items()
            if name.endswith("SUBPATH") and path.startswith("docs-private/")
        ]
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "glob"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            pattern = node.args[0].value
            if not any(char in pattern for char in "*?["):
                continue
            base = _private_path(_resolve_path(node.func.value, aliases))
            if base is None and len(subpaths) == 1:
                base = subpaths[0]
            if base is not None:
                specs.append(((PurePosixPath(base) / pattern).as_posix(), source))
    return sorted(set(specs), key=lambda item: (item[0], str(item[1])))


def _files(root: Path, pattern: str) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.glob(pattern)
        if path.is_file()
    }


def _tracked_papers(root: Path) -> set[str]:
    return {
        path.parent.name
        for path in (root / "tests" / "fixtures" / "corpus").glob(
            "*/benchmark-fixture.yaml"
        )
    }


def _profile_paths(
    root: Path, manifest_paths: set[str], grind_dir: str
) -> set[str]:
    if not manifest_paths:
        raise RuntimeError("no directly read C6 manifests found in tests")
    profiles = set()
    for path in manifest_paths:
        payload = json.loads((root / path).read_text(encoding="utf-8"))
        profiles.update(
            (PurePosixPath(grind_dir) / job["profile"]).as_posix()
            for job in payload.get("jobs", [])
        )
    return profiles


def derive_manifest(root: Path = ROOT) -> dict[str, Any]:
    path_readers, trees, aliases = _readers(root)
    globs = _glob_specs(root, trees, aliases)
    c6_pattern = next(
        path for path, _source in globs
        if path == "docs-private/grind/manifest-c6-*.json"
    )
    literature_pattern = next(
        path for path, _source in globs
        if path == "docs-private/deep-research/literature/*/benchmark-fixture.yaml"
    )
    c6_reader_source = next(source for path, source in globs if path == c6_pattern)
    literature_source = next(source for path, source in globs if path == literature_pattern)
    c6_tree = trees[c6_reader_source]
    c6_names = {
        node.value
        for node in ast.walk(c6_tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and MANIFEST_NAME.fullmatch(node.value)
    }
    c6_paths = {
        (PurePosixPath("docs-private/grind") / name).as_posix()
        for name in c6_names
    } | _files(root, c6_pattern)

    c6_manifest_paths = {
        path
        for path in path_readers
        if path.startswith("docs-private/grind/manifest-c6-") and path.endswith(".json")
    }
    profiles = _profile_paths(root, c6_manifest_paths, "docs-private/grind")
    profile_readers = sorted(
        {c6_reader_source.relative_to(root).as_posix(), "scripts/epoch_grind.py"}
    )

    tracked = _tracked_papers(root)
    corpus_files = {
        path
        for path in _files(root, literature_pattern)
        if PurePosixPath(path).parent.name not in tracked
    }
    literature = corpus_files
    corpus_readers = [literature_source.relative_to(root).as_posix()]

    entries: list[dict[str, Any]] = []
    corpus_root = PurePosixPath(literature_pattern).parts[:-2]
    corpus_root_path = PurePosixPath(*corpus_root).as_posix()
    if corpus_root_path in path_readers:
        entries.append(
            {
                "kind": "directory",
                "path": f"{corpus_root_path}/",
                "readers": corpus_readers,
                "copy": False,
            }
        )

    for path, readers in sorted(path_readers.items()):
        if path in c6_paths or path in profiles or path in literature:
            continue
        if path.startswith("docs-private/deep-research/literature/") and path.endswith(
            "/benchmark-fixture.yaml"
        ) and PurePosixPath(path).parent.name in tracked:
            continue
        if PurePosixPath(path).suffix:
            entries.append(
                {"kind": "file", "path": path, "readers": sorted(readers)}
            )

    c6_readers = [c6_reader_source.relative_to(root).as_posix()]
    entries.append(
        {
            "kind": "glob",
            "path": c6_pattern,
            "readers": c6_readers,
            "required": [
                {
                    "path": path,
                    "readers": sorted(path_readers.get(path, set()) | set(c6_readers)),
                }
                for path in sorted(c6_paths)
            ],
        }
    )
    entries.extend(
        {"kind": "file", "path": path, "readers": profile_readers}
        for path in sorted(profiles)
    )
    entries.append(
        {
            "kind": "glob",
            "path": literature_pattern,
            "readers": corpus_readers,
            "required": [
                {"path": path, "readers": corpus_readers} for path in sorted(literature)
            ],
        }
    )
    known_patterns = {c6_pattern, literature_pattern}
    extra_patterns = sorted(
        {pattern for pattern, _source in globs} - known_patterns
    )
    for pattern in extra_patterns:
        required_paths = _files(root, pattern)
        readers = sorted(
            {
                source.relative_to(root).as_posix()
                for candidate, source in globs
                if candidate == pattern
            }
        )
        entries.append(
            {
                "kind": "glob",
                "path": pattern,
                "readers": readers,
                "required": [
                    {
                        "path": path,
                        "readers": readers,
                    }
                    for path in sorted(required_paths)
                ],
            }
        )
    entries.sort(key=lambda entry: (entry["path"], entry["kind"]))

    conftest = trees[root / "conftest.py"]
    strict_env = next(
        node.value
        for names, value in _assignments(conftest)
        if "DOCS_PRIVATE_MANIFEST_STRICT_ENV" in names
        for node in [value]
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    return {
        "schema_version": 1,
        "strict_env": f"{strict_env}=1",
        "entries": entries,
    }


def main() -> None:
    # Sparse seats omit docs-private, so dynamic expansions may be incomplete.
    print(json.dumps(derive_manifest(), indent=2))


if __name__ == "__main__":
    main()
