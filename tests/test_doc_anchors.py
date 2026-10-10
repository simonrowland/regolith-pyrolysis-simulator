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


def test_b718_sweep_outputs_are_retained_without_the_executable() -> None:
    script = ROOT / "docs" / "battery" / "b718-sweep.py"
    assert not script.exists()

    outputs = (
        "b718-sweep-at-8089eadbf.md",
        "b718-sweep-after-fix.md",
        "b718-class-b-rows-at-8089eadbf.csv",
        "b718-class-b-rows-after-fix.csv",
    )
    assert all((ROOT / "docs" / "battery" / output).is_file() for output in outputs[:2])
    assert all((ROOT / "docs" / "battery" / output).is_file() for output in outputs[2:])

    for report_name in ("b718-sweep-at-8089eadbf.md", "b718-sweep-after-fix.md"):
        report = (ROOT / "docs" / "battery" / report_name).read_text(encoding="utf-8")
        assert "Worktree: `.`" in report
        assert "`b718-sweep/class-b-rows.csv`" in report
        assert "`docs/battery/b718-sweep.py`" in report
        assert ("/" + "workspace/") not in report
