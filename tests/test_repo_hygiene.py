"""Repo hygiene guards: paths that must never be git-tracked.

``docs-private/`` is gitignored — machine-local review notes and worker
mutation scripts. On 2026-09-21 workers bypassed the ignore rule with
``git add -f`` and 61 private files were pushed (untracked in 28546a466).

Worker-facing convention: mutation scripts and private reviews live in
``docs-private/`` untracked. Never use ``git add -f`` there. This test makes
that mistake fail locally instead of reaching the remote.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def test_docs_private_is_never_git_tracked():
    if shutil.which("git") is None:
        pytest.skip("git binary not available")
    inside = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("not a git checkout")

    proc = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "docs-private"],
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = [line for line in proc.stdout.splitlines() if line.strip()]
    assert not tracked, (
        "docs-private/ is gitignored and must stay untracked; "
        "untrack with `git rm -r --cached docs-private` "
        "(never `git add -f` there):\n" + "\n".join(tracked)
    )
