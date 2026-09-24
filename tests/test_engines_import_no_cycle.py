"""Keep every engines module importable without entering simulator imports."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import yaml

from engines.yaml_loader import YAML12SafeLoader


def _engine_module_names() -> list[str]:
    engines_root = Path(__file__).parents[1] / "engines"
    modules: set[str] = set()
    for path in engines_root.rglob("*.py"):
        parts = list(path.relative_to(engines_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules.add("engines" + ("." + ".".join(parts) if parts else ""))
    return sorted(modules)


def test_each_engines_module_imports_in_a_fresh_interpreter() -> None:
    repo_root = Path(__file__).parents[1]
    for module in _engine_module_names():
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"fresh-interpreter import failed for {module}:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )


def test_engines_yaml_loader_keeps_yaml12_booleans_and_parent_resolvers() -> None:
    base_loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    parsed = yaml.load(
        "yes_value: yes\n"
        "no_value: NO\n"
        "on_value: on\n"
        "true_value: true\n"
        "false_value: FALSE\n",
        Loader=YAML12SafeLoader,
    )

    assert parsed == {
        "yes_value": "yes",
        "no_value": "NO",
        "on_value": "on",
        "true_value": True,
        "false_value": False,
    }
    assert YAML12SafeLoader.yaml_implicit_resolvers is not (
        base_loader.yaml_implicit_resolvers
    )
    assert yaml.load("value: yes\n", Loader=base_loader)["value"] is True
