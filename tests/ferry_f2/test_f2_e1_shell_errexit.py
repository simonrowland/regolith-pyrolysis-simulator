"""F2 root E1: collect / enginepatch fail closed on total miss / empty refresh."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_collect_recipe_db_exits_nonzero_when_zero_studies() -> None:
    script = ROOT / "scripts" / "collect_recipe_db.sh"
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            ["bash", str(script), tmp, "no-such-host-f2.invalid"],
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode != 0


def test_enginepatch_refresh_refuses_missing_checkout() -> None:
    script = ROOT / "patches" / "scripts" / "enginepatch.sh"
    env = {**os.environ, "SULFLIQ_CHECKOUT": "/tmp/f2-no-such-sulfliq-checkout"}
    proc = subprocess.run(
        ["bash", str(script), "refresh", "sulfliq"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode != 0


def test_enginepatch_refresh_refuses_non_git_dir(tmp_path: Path) -> None:
    script = ROOT / "patches" / "scripts" / "enginepatch.sh"
    checkout = tmp_path / "sulfliq"
    checkout.mkdir()
    env = {**os.environ, "SULFLIQ_CHECKOUT": str(checkout)}
    proc = subprocess.run(
        ["bash", str(script), "refresh", "sulfliq"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode != 0


def test_scripts_enable_errexit() -> None:
    collect = (ROOT / "scripts" / "collect_recipe_db.sh").read_text(encoding="utf-8")
    patch = (ROOT / "patches" / "scripts" / "enginepatch.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in collect
    assert "set -euo pipefail" in patch
