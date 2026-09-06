"""Drift gate for the tracked empirical-corpus index."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPO_ROOT / "data" / "literature" / "build_index.py"
COMMITTED_YAML = REPO_ROOT / "data" / "literature" / "INDEX.yaml"
COMMITTED_MD = REPO_ROOT / "data" / "literature" / "INDEX.md"


def test_regenerated_index_matches_committed(tmp_path: Path) -> None:
    """Rebuilding INDEX.yaml/md into a temp dir must match the committed files.

    Null hypothesis: the committed index can drift from the builder without CI
    noticing. Regenerating into tmp (not mutating the tree) and comparing bytes
    makes that drift fail this test.
    """
    assert BUILDER.is_file()
    assert COMMITTED_YAML.is_file()
    assert COMMITTED_MD.is_file()
    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--root",
            str(REPO_ROOT),
            "--out-dir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    got_yaml = tmp_path / "INDEX.yaml"
    got_md = tmp_path / "INDEX.md"
    assert got_yaml.read_bytes() == COMMITTED_YAML.read_bytes()
    assert got_md.read_bytes() == COMMITTED_MD.read_bytes()
