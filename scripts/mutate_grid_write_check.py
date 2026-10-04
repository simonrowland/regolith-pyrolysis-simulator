#!/usr/bin/env python3
"""Run the full grid test file against isolated write-check mutants."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = REPO_ROOT.parent.parent / ".venv" / "bin" / "python"
CHECK = """        if not (
            is_failure and result_model == ENGINE_MODEL_UNAVAILABLE
        ) and queued_model != result_model:
            raise ValueError(
                "grid result model differs from queued key: "
                f"queued={queued_model!r}, result={result_model!r}"
            )
"""
ERROR_BODY = """            raise ValueError(
                "grid result model differs from queued key: "
                f"queued={queued_model!r}, result={result_model!r}"
            )
"""

MUTATIONS = (
    "invoked_false_exemption",
    "any_failure_exemption",
    "any_unavailable_exemption",
    "producer_unavailable_with_backend",
    "remove_model_check",
)


def _mutate(fullname: str, source: str, mutation: str) -> str:
    if fullname == "scripts.grid_pregrind_writer":
        if mutation == "invoked_false_exemption":
            replacement = (
                "        backend_invoked = bool(\n"
                "            json.loads(str(output[\"raw_payload\"])).get(\"engine_invoked\")\n"
                "        )\n"
                "        if not (is_failure and not backend_invoked) and queued_model != result_model:\n"
                + ERROR_BODY
            )
        elif mutation == "any_failure_exemption":
            replacement = "        if not is_failure and queued_model != result_model:\n" + ERROR_BODY
        elif mutation == "any_unavailable_exemption":
            replacement = (
                "        if result_model != ENGINE_MODEL_UNAVAILABLE "
                "and queued_model != result_model:\n" + ERROR_BODY
            )
        elif mutation == "remove_model_check":
            replacement = ""
        else:
            return source
        if CHECK not in source:
            raise RuntimeError("writer model-check anchor not found")
        return source.replace(CHECK, replacement, 1)

    if (
        fullname == "scripts.grid_pregrind"
        and mutation == "producer_unavailable_with_backend"
    ):
        anchor = "    if backend is None:\n        return ENGINE_MODEL_UNAVAILABLE\n"
        if anchor not in source:
            raise RuntimeError("producer model anchor not found")
        return source.replace(anchor, "    return ENGINE_MODEL_UNAVAILABLE\n", 1)
    return source


def _sitecustomize(root: Path) -> str:
    return f'''\
from scripts.mutate_grid_write_check import _mutate
import importlib.abc
import importlib.machinery
import os
import sys

MUTATION = os.environ["GRID_WRITE_MUTATION"]

class MutatingLoader(importlib.abc.Loader):
    def __init__(self, loader):
        self.loader = loader
    def create_module(self, spec):
        return self.loader.create_module(spec) if hasattr(self.loader, "create_module") else None
    def exec_module(self, module):
        source = self.loader.get_source(module.__name__)
        if source is None:
            raise ImportError("source unavailable for mutation test")
        filename = self.loader.get_filename(module.__name__)
        mutated = _mutate(module.__name__, source, MUTATION)
        exec(compile(mutated, filename, "exec"), module.__dict__)

class MutatingFinder(importlib.abc.MetaPathFinder):
    targets = {{"scripts.grid_pregrind", "scripts.grid_pregrind_writer"}}
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.targets:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and spec.loader is not None:
            spec.loader = MutatingLoader(spec.loader)
        return spec

sys.meta_path.insert(0, MutatingFinder())
'''


def main() -> int:
    results: list[tuple[str, list[str]]] = []
    with tempfile.TemporaryDirectory(prefix="grid-write-mutation-") as temp_dir:
        sitecustomize = Path(temp_dir)
        (sitecustomize / "sitecustomize.py").write_text(_sitecustomize(REPO_ROOT))
        for mutation in MUTATIONS:
            env = os.environ.copy()
            env["GRID_WRITE_MUTATION"] = mutation
            env["PYTHONPATH"] = os.pathsep.join(
                (str(sitecustomize), str(REPO_ROOT), env.get("PYTHONPATH", ""))
            )
            completed = subprocess.run(
                [
                    str(PYTHON), "-m", "pytest", "-q", "--tb=no",
                    "tests/test_grid_pregrind.py",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            output = completed.stdout + completed.stderr
            failed = re.findall(
                r"FAILED tests/test_grid_pregrind\.py::([^\s]+)", output
            )
            if completed.returncode == 0:
                failed = []
            results.append((mutation, failed))
            state = "RED" if failed else "SURVIVED"
            print(
                f"{state} {mutation}: "
                f"{', '.join(failed) if failed else 'no failing tests'}"
            )
    return 0 if all(failed for _mutation, failed in results) else 1


if __name__ == "__main__":
    sys.exit(main())
