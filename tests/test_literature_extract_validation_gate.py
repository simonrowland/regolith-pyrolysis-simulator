"""Fail the PR gate when any committed literature extract is invalid.

``test_repo_extracts_validate_green`` already called
``validate_all()`` over ``discover_extracts()``. That did not catch the
b-539 extracts: fidelity matching stayed off (the extract gate always
runs it), the assertion kept only the first 20 errors, and the walk was
one test on the 300 s CPU budget, so the gate reported a timeout instead
of the invalid files. A partial checkout still passed, because discovery
only sees files present on disk and an empty result is the only missing
input ``validate_all`` rejects.

This module is that gate. Each family stays under the budget. The
validator call is ``validate_all(..., check_fidelity_match=True)``, the
file entry that wraps the same document and fidelity checks
``tools/extract_gate.py`` runs, including companion tables.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import validate_literature_extracts as validator

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS_RELATIVE = Path("data/literature/extracts")

# Sized from a 20-file sample that included the eight largest extracts
# (61 s CPU) and from the historical single-call timeout at 300 s.
# Prefix buckets keep each test under that budget without dropping a file.
FAMILIES = ("a-e", "f-j", "k-n", "kems", "o-s", "t-z")


def family_of(path: Path) -> str:
    stem = path.stem
    if stem.startswith("kems-"):
        return "kems"
    letter = stem[0].lower()
    if letter in "abcde":
        return "a-e"
    if letter in "fghij":
        return "f-j"
    if letter in "klmn":
        return "k-n"
    if letter in "opqrs":
        return "o-s"
    if letter in "tuvwxyz0123456789":
        return "t-z"
    raise AssertionError(f"no extract family for {path}")


def committed_extract_paths() -> list[Path]:
    """Git-tracked extract YAML, or a failure when that input is missing."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--", str(EXTRACTS_RELATIVE)],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")

    paths: list[Path] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8"))
        if relative.parent != EXTRACTS_RELATIVE:
            continue
        if not validator._is_extract_path(relative):
            continue
        paths.append(REPO_ROOT / relative)

    assert paths, f"no committed extracts under {EXTRACTS_RELATIVE}"
    missing = [path for path in paths if not path.is_file()]
    assert not missing, "committed extracts missing from the worktree:\n" + "\n".join(
        str(path) for path in missing
    )
    return sorted(paths)


def assert_extracts_valid(paths: list[Path]) -> None:
    errors = validator.validate_all(paths, check_fidelity_match=True)
    assert errors == [], "\n".join(errors)


def test_invalid_extract_fails_the_gate(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid-extract.yaml"
    invalid.write_text(
        "schema_version: invalid\nsource_id: invalid-extract\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError) as failure:
        assert_extracts_valid([invalid])
    assert str(invalid) in str(failure.value)


def test_committed_extracts_match_validator_discovery() -> None:
    committed = committed_extract_paths()
    discovered = validator.discover_extracts()
    assert set(discovered) == set(committed)
    assert {family_of(path) for path in committed} == set(FAMILIES)


@pytest.mark.parametrize("family", FAMILIES)
def test_committed_extracts_in_family_validate(family: str) -> None:
    paths = [path for path in committed_extract_paths() if family_of(path) == family]
    assert paths, f"extract family {family!r} matched no committed extracts"
    assert_extracts_valid(paths)
