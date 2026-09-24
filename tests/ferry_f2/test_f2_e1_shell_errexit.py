"""F2 root E1: collect / enginepatch fail closed on total miss / empty refresh."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_git_checkout(path: Path, filename: str, contents: str) -> str:
    path.mkdir(parents=True)
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "enginepatch-test@example.invalid", cwd=path)
    _git("config", "user.name", "enginepatch test", cwd=path)
    (path / filename).parent.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(contents, encoding="utf-8")
    _git("add", filename, cwd=path)
    _git("commit", "-qm", "base", cwd=path)
    return _git("rev-parse", "HEAD", cwd=path)


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


def test_enginepatch_verify_follows_editable_import_location(tmp_path: Path) -> None:
    """Verify the imported checkout, including staged drift, not a sibling."""

    project = tmp_path / "project"
    script = project / "patches" / "scripts" / "enginepatch.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "patches" / "scripts" / "enginepatch.sh", script)

    loaded_checkout = tmp_path / "loaded" / "VapoRock"
    package = loaded_checkout / "src" / "vaporock"
    base_sha = _init_git_checkout(
        loaded_checkout,
        "src/vaporock/__init__.py",
        "LOADED = True\n",
    )

    # This is the checkout the old sibling resolver would inspect. Make its
    # working tree drift so following it would fail verification.
    stale_sibling = tmp_path / "VapoRock"
    _init_git_checkout(stale_sibling, "engine.py", "STALE = True\n")
    (stale_sibling / "engine.py").write_text("STALE = False\n", encoding="utf-8")

    patch_dir = project / "patches" / "vaporock"
    patch_dir.mkdir(parents=True)
    (patch_dir / "UPSTREAM.pin").write_text(
        f"base_sha: {base_sha}\n", encoding="utf-8"
    )
    (patch_dir / "0001-test.patch").write_text("", encoding="utf-8")

    # Model an editable install: a .pth imports a generated finder that maps
    # vaporock to the source tree outside the project checkout. PYTHONPATH does
    # not process .pth files itself, so sitecustomize executes this one exactly
    # as the interpreter's site-package setup would.
    editable_site = tmp_path / "editable-site"
    editable_site.mkdir()
    finder = "__editable___vaporock_finder"
    (editable_site / f"{finder}.py").write_text(
        "from importlib.machinery import PathFinder\n"
        "import sys\n"
        "class _EditableFinder:\n"
        "    @classmethod\n"
        "    def find_spec(cls, fullname, path=None, target=None):\n"
        "        if fullname == 'vaporock':\n"
        f"            return PathFinder.find_spec(fullname, [{str(package.parent)!r}])\n"
        "        return None\n"
        "sys.meta_path.insert(0, _EditableFinder)\n",
        encoding="utf-8",
    )
    (editable_site / f"{finder}.pth").write_text(
        f"import {finder}\n", encoding="utf-8"
    )
    (editable_site / "sitecustomize.py").write_text(
        f"exec(open({str(editable_site / f'{finder}.pth')!r}).read(), {{}})\n",
        encoding="utf-8",
    )

    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    env = {
        **os.environ,
        "PYTHONPATH": str(editable_site),
        "TMPDIR": str(temp_dir),
    }
    verify = [
        "bash",
        str(script),
        "--python",
        sys.executable,
        "verify",
        "vaporock",
    ]
    proc = subprocess.run(
        verify,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "vaporock: MATCH" in proc.stdout
    assert f"RESOLVED={loaded_checkout}" in proc.stdout
    assert str(stale_sibling) not in proc.stdout

    (loaded_checkout / "src/vaporock/__init__.py").write_text(
        "LOADED = False\n", encoding="utf-8"
    )
    _git("add", "src/vaporock/__init__.py", cwd=loaded_checkout)
    staged_drift = subprocess.run(
        verify,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert staged_drift.returncode != 0
    assert "vaporock: DRIFT" in staged_drift.stdout


def test_enginepatch_verify_does_not_match_unloaded_sulfliq_fallback(
    tmp_path: Path,
) -> None:
    """A matching historical fallback is not evidence for an unloaded engine."""

    project = tmp_path / "project"
    script = project / "patches" / "scripts" / "enginepatch.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "patches" / "scripts" / "enginepatch.sh", script)

    home = tmp_path / "home"
    fallback = home / "Repos" / "sulfliq"
    base_contents = "LOADED = False\n"
    base_file = fallback / "src" / "SulfLiq" / "__init__.py"
    base_sha = _init_git_checkout(
        fallback,
        "src/SulfLiq/__init__.py",
        base_contents,
    )
    base_file.write_text("LOADED = True\n", encoding="utf-8")
    patch_text = _git("diff", cwd=fallback)

    patch_dir = project / "patches" / "sulfliq"
    patch_dir.mkdir(parents=True)
    (patch_dir / "UPSTREAM.pin").write_text(
        f"base_sha: {base_sha}\n", encoding="utf-8"
    )
    (patch_dir / "0001-test.patch").write_text(patch_text, encoding="utf-8")

    no_import_python = tmp_path / "no-import-python"
    no_import_python.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -S \"$@\"\n",
        encoding="utf-8",
    )
    no_import_python.chmod(0o755)
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PYTHONPATH": "",
        "TMPDIR": str(temp_dir),
    }
    verify = [
        "bash",
        str(script),
        "--python",
        str(no_import_python),
        "verify",
        "sulfliq",
    ]

    proc = subprocess.run(
        verify,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert proc.returncode != 0
    assert f"sulfliq: NOT-LOADED ({no_import_python})" in proc.stdout
    assert "FALLBACK=MATCH" in proc.stdout
    assert "sulfliq: MATCH" not in proc.stdout
    assert f"RESOLVED={fallback}" in proc.stdout

    allowed = subprocess.run(
        ["bash", str(script), "--allow-not-loaded", *verify[2:]],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr

    overridden = subprocess.run(
        verify,
        capture_output=True,
        text=True,
        check=False,
        env={**env, "SULFLIQ_CHECKOUT": str(fallback)},
    )
    assert overridden.returncode != 0
    assert (
        f"sulfliq: ASSERTED PATCH=MATCH RESOLVED={fallback} "
        "(explicit SULFLIQ_CHECKOUT override; not import-verified)"
        in overridden.stdout
    )

    asserted_allowed = subprocess.run(
        ["bash", str(script), "--allow-asserted", *verify[2:]],
        capture_output=True,
        text=True,
        check=False,
        env={**env, "SULFLIQ_CHECKOUT": str(fallback)},
    )
    assert asserted_allowed.returncode == 0, (
        asserted_allowed.stdout + asserted_allowed.stderr
    )
    assert "sulfliq: ASSERTED PATCH=MATCH" in asserted_allowed.stdout


def test_scripts_enable_errexit() -> None:
    collect = (ROOT / "scripts" / "collect_recipe_db.sh").read_text(encoding="utf-8")
    patch = (ROOT / "patches" / "scripts" / "enginepatch.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in collect
    assert "set -euo pipefail" in patch
