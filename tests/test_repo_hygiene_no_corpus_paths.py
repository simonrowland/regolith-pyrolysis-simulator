"""Keep private source-corpus artefacts out of the public repository."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _offending_paths(paths: list[str]) -> list[str]:
    """Return tracked paths that belong in the private source corpus."""
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


def test_tracked_files_do_not_include_corpus_paths() -> None:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=str(REPO_ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        pytest.skip(f"git ls-files unavailable; a non-git copy cannot be checked ({exc})")
    if result.returncode:
        pytest.skip(
            "git ls-files unavailable; a non-git copy cannot be checked "
            f"(exit {result.returncode}: {result.stderr.strip()})"
        )

    offending = _offending_paths(result.stdout.splitlines())
    assert not offending, (
        "Tracked corpus artefacts must not be committed here:\n"
        + "\n".join(offending)
        + "\nThe corpus is the place for source artefacts, and the extract YAML "
        "is the place for transcribed values."
    )


def test_top_level_tables_path_is_rejected_by_same_predicate() -> None:
    assert _offending_paths(["tables/x/t1.csv"]) == ["tables/x/t1.csv"]
