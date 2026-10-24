from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
from pathlib import Path
from typing import Any


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


def _auditor_with_anchors(tmp_path: Path, anchors: tuple[str, ...]) -> tuple[Any, Path]:
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
    (tmp_path / "owner.py").write_text(
        "def exists():\n    return None\n",
        encoding="utf-8",
    )
    doc_path = docs_dir / "anchors.md"
    doc_path.write_text(
        "## 1. Anchors\n" + "\n".join(anchors) + "\n",
        encoding="utf-8",
    )
    auditor.ROOT = tmp_path
    auditor.DOCS_DIR = docs_dir
    return auditor, doc_path


def test_drifted_hint_warns_and_refreshes(tmp_path: Path) -> None:
    auditor, doc_path = _auditor_with_anchors(
        tmp_path,
        ("<!-- impl: §1 -> owner.py exists:3 — existing owner -->",),
    )
    initial_output = io.StringIO()
    with contextlib.redirect_stdout(initial_output):
        assert auditor.main([]) == 0
    output = initial_output.getvalue()
    assert "WARNING" in output
    assert "line hint 3 outside resolved 1-2" in output
    assert "python3 scripts/audit_doc_anchors.py --refresh" in output
    assert "doc anchor audit: PASS (1 anchor(s))" in output

    refresh_output = io.StringIO()
    with contextlib.redirect_stdout(refresh_output):
        assert auditor.main(["--refresh"]) == 0
    assert "refreshed 1 drifted line hint(s)" in refresh_output.getvalue()
    assert "exists:1 — existing owner" in doc_path.read_text(encoding="utf-8")


def test_refresh_fails_on_missing_and_malformed_anchors(tmp_path: Path) -> None:
    auditor, _ = _auditor_with_anchors(
        tmp_path,
        (
            "<!-- impl: §1 -> owner.py exists:999 — existing owner -->",
            "<!-- impl: §1 -> owner.py missing:2 — missing symbol -->",
            "<!-- impl: §1 -> owner.py:3 — malformed anchor -->",
        ),
    )
    audit_output = io.StringIO()
    with contextlib.redirect_stdout(audit_output):
        assert auditor.main([]) == 1
    assert "symbol not found: missing" in audit_output.getvalue()
    assert "malformed impl anchor" in audit_output.getvalue()

    refresh_output = io.StringIO()
    with contextlib.redirect_stdout(refresh_output):
        assert auditor.main(["--refresh"]) == 1
    output = refresh_output.getvalue()
    assert "refreshed 1 drifted line hint(s)" in output
    assert "symbol not found: missing" in output
    assert "malformed impl anchor" in output


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
