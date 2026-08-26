"""Self-test pin for tools/normalize_extract_store.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "normalize_extract_store.py"


def test_normalize_extract_store_self_test() -> None:
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--self-test"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "self-test OK" in proc.stdout
