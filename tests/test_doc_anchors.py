from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_doc_impl_anchors_resolve() -> None:
    """Pin implementation anchors to the symbols that currently own them."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit_doc_anchors.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_refresh_reports_missing_and_malformed_anchors(tmp_path: Path) -> None:
    script_path = ROOT / "scripts" / "audit_doc_anchors.py"
    spec = importlib.util.spec_from_file_location("audit_doc_anchors_test", script_path)
    assert spec is not None and spec.loader is not None
    auditor = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = auditor
    try:
        spec.loader.exec_module(auditor)
    finally:
        sys.modules.pop(spec.name, None)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (tmp_path / "owner.py").write_text("def exists():\n    return None\n", encoding="utf-8")
    doc_path = docs_dir / "anchors.md"
    doc_path.write_text(
        "## 1. Anchors\n"
        "<!-- impl: §1 -> owner.py exists:999 — existing owner -->\n"
        "<!-- impl: §1 -> owner.py missing:2 — missing symbol -->\n"
        "<!-- impl: §1 -> owner.py:3 — malformed anchor -->\n",
        encoding="utf-8",
    )
    auditor.ROOT = tmp_path
    auditor.DOCS_DIR = docs_dir

    initial_output = io.StringIO()
    with contextlib.redirect_stdout(initial_output):
        assert auditor.main([]) == 1
    assert "python3 scripts/audit_doc_anchors.py --refresh" in initial_output.getvalue()

    refresh_output = io.StringIO()
    with contextlib.redirect_stdout(refresh_output):
        assert auditor.main(["--refresh"]) == 1
    output = refresh_output.getvalue()
    assert "symbol not found: missing" in output
    assert "malformed impl anchor" in output
    assert "exists:1 — existing owner" in doc_path.read_text(encoding="utf-8")


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
