from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "profile_eval_hotpath.py"


def test_profile_eval_hotpath_imports() -> None:
    spec = importlib.util.spec_from_file_location("profile_eval_hotpath", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DEFAULT_REPEAT >= 3
    assert module.DEFAULT_HIGH_HOURS == 1
    assert "evaluate_internal_analytical_repeat" in module.SCENARIO_ORDER


def test_profile_eval_hotpath_help() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "evaluate_internal_analytical_1h" in result.stdout
    assert "evaluate_internal_analytical_repeat" in result.stdout
    assert "--high-hours" in result.stdout
