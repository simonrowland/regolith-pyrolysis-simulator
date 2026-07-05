from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_doc_impl_anchors_resolve() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit_doc_anchors.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
