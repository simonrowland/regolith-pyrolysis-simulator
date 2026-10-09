from __future__ import annotations

import ast
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


def test_b718_sweep_uses_relative_artifact_paths() -> None:
    script = ROOT / "docs" / "battery" / "b718-sweep.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    output_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "OUT" for target in node.targets)
    )
    output_name = ast.literal_eval(output_assignment.value.args[0])
    assert not Path(output_name).is_absolute()

    for report_name in ("b718-sweep-at-8089eadbf.md", "b718-sweep-after-fix.md"):
        report = (ROOT / "docs" / "battery" / report_name).read_text(encoding="utf-8")
        assert "Worktree: `.`" in report
        assert "`b718-sweep/class-b-rows.csv`" in report
        assert "`docs/battery/b718-sweep.py`" in report
        assert ("/" + "workspace/") not in report
