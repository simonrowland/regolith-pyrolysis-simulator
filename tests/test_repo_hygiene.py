"""Repo hygiene guards: paths that must never be git-tracked.

``docs-private/`` is gitignored — machine-local review notes and worker
mutation scripts. On 2026-09-21 workers bypassed the ignore rule with
``git add -f`` and 61 private files were pushed (untracked in 28546a466).

Worker-facing convention: mutation scripts and private reviews live in
``docs-private/`` untracked. Never use ``git add -f`` there. This test makes
that mistake fail locally instead of reaching the remote.
"""

import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

REPO = Path(__file__).resolve().parent.parent


def _tracked_paths(repo: Path = REPO) -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git binary not available")
    inside = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("not a git checkout")

    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [os.fsdecode(path) for path in proc.stdout.split(b"\0") if path]


def _offending_paths(paths: list[str]) -> list[str]:
    offending = []
    for path in paths:
        parts = PurePosixPath(path).parts
        if (
            parts[0] in {"raw", "text", "tables", "ocr", "docs-private", ".agent-scratch"}
            or PurePosixPath(path).name.endswith(".pdf")
            or "pdftotext" in path
        ):
            offending.append(path)
    return offending


def test_docs_private_is_never_git_tracked():
    offending = _offending_paths(_tracked_paths())
    tracked = [
        path for path in offending if path == "docs-private" or path.startswith("docs-private/")
    ]
    assert not tracked, (
        "docs-private/ is gitignored and must stay untracked; "
        "untrack with `git rm -r --cached docs-private` "
        "(never `git add -f` there):\n"
        + "\n".join(tracked)
        + "\nThe corpus is the place for source artefacts, and the extract YAML "
        "is the place for transcribed values."
    )

    corpus_paths = [
        path
        for path in offending
        if path != "docs-private" and not path.startswith("docs-private/")
    ]
    assert not corpus_paths, (
        "Tracked corpus artefacts must not be committed here:\n"
        + "\n".join(corpus_paths)
        + "\nThe corpus is the place for source artefacts, and the extract YAML "
        "is the place for transcribed values."
    )


def test_corpus_path_predicate_rejects_synthetic_path():
    assert _offending_paths(["tables/x/t1.csv"]) == ["tables/x/t1.csv"]


def test_nul_delimited_git_paths_preserve_quoted_corpus_names(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    corpus_paths = [
        "tables/échantillon/t1.csv",
        "tables/x/tab\tname.csv",
        "docs/references/échantillon.pdf",
    ]
    for relative_path in corpus_paths:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "--all"], check=True)

    tracked = _tracked_paths(tmp_path)
    assert all(path in tracked for path in corpus_paths)
    assert set(_offending_paths(tracked)) == set(corpus_paths)
