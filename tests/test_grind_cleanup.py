from __future__ import annotations

from pathlib import Path

import pytest

from scripts import grind_cleanup


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True)
    return path


def test_cleanup_selects_only_allowlisted_grind_outputs(tmp_path: Path) -> None:
    selected = _mkdir(tmp_path / "dose-exploration-alpha")
    _mkdir(tmp_path / "unrelated")
    _mkdir(tmp_path / ".ssh")
    _mkdir(tmp_path / ".git")
    _mkdir(tmp_path / ".venv")
    _mkdir(tmp_path / "docs-private")

    plan = grind_cleanup.build_cleanup_plan([tmp_path])

    assert [candidate.path for candidate in plan.candidates] == [selected]
    assert {refusal.path.name for refusal in plan.refusals} == set()


def test_cleanup_denylist_vetoes_protected_repo_even_when_allowlisted(tmp_path: Path) -> None:
    protected_repo = _mkdir(tmp_path / "grind-c6-owned")
    _mkdir(protected_repo / ".git")

    plan = grind_cleanup.build_cleanup_plan([tmp_path], repo_root=protected_repo)

    assert plan.candidates == ()
    assert [(refusal.path, refusal.reason) for refusal in plan.refusals] == [
        (protected_repo, f"protected path: {protected_repo.resolve(strict=False)}")
    ]


@pytest.mark.parametrize("protected_name", [".git", ".venv", "docs-private"])
def test_cleanup_denylist_vetoes_allowlisted_dir_containing_protected_paths(
    tmp_path: Path,
    protected_name: str,
) -> None:
    candidate = _mkdir(tmp_path / "grind-c6-project")
    _mkdir(candidate / protected_name)

    plan = grind_cleanup.build_cleanup_plan([tmp_path])

    assert plan.candidates == ()
    assert [(refusal.path, refusal.reason) for refusal in plan.refusals] == [
        (candidate, f"contains protected path: {candidate / protected_name}")
    ]
    assert candidate.exists()


def test_cleanup_denylist_vetoes_symlinked_allowlist_candidate(tmp_path: Path) -> None:
    target = _mkdir(tmp_path / "target")
    link = tmp_path / "grind-c6-link"
    link.symlink_to(target, target_is_directory=True)

    plan = grind_cleanup.build_cleanup_plan([tmp_path])

    assert plan.candidates == ()
    assert [(refusal.path, refusal.reason) for refusal in plan.refusals] == [(link, "symlink")]


def test_cleanup_dry_run_is_default_and_does_not_delete(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    candidate = _mkdir(tmp_path / "regolith-cache-archive-2026")

    exit_code = grind_cleanup.main(["--search-root", str(tmp_path)])

    assert exit_code == 0
    assert candidate.exists()
    output = capsys.readouterr().out
    assert "mode=DRY-RUN" in output
    assert f"WOULD_DELETE {candidate}" in output


def test_cleanup_yes_deletes_allowlisted_temp_fixture(tmp_path: Path) -> None:
    candidate = _mkdir(tmp_path / "recipe-db-collected")

    exit_code = grind_cleanup.main(["--search-root", str(tmp_path), "--yes"])

    assert exit_code == 0
    assert not candidate.exists()


def test_cleanup_refusal_returns_nonzero_and_does_not_delete(tmp_path: Path) -> None:
    protected_repo = _mkdir(tmp_path / "grind-c6-repo")
    _mkdir(protected_repo / ".git")

    exit_code = grind_cleanup.main(["--search-root", str(tmp_path), "--yes"])

    assert exit_code == 2
    assert protected_repo.exists()
