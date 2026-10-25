"""Shared guard for tests whose proof requires repository history."""

from pathlib import Path
import subprocess

import pytest


def require_git_history(root: Path, *revisions: str) -> None:
    """Skip only when this checkout or a requested commit is unavailable."""
    try:
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("missing_git_history: git executable is unavailable")
    except subprocess.CalledProcessError as exc:
        if "not a git repository" not in (exc.stderr or "").lower():
            raise
        pytest.skip(f"missing_git_history: not a git work tree: {exc.stderr.strip()}")

    for revision in revisions:
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "rev-parse",
                    "--verify",
                    f"{revision}^{{commit}}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").lower()
            if "ambiguous" in stderr:
                raise
            if not any(
                marker in stderr
                for marker in (
                    "needed a single revision",
                    "unknown revision",
                    "bad revision",
                )
            ):
                raise
            pytest.skip(
                f"missing_git_history: required revision {revision!r} is unreachable: "
                f"{exc.stderr.strip()}"
            )


def require_store_neutral_ack_history(root: Path, freshness, *revisions: str) -> None:
    """Use the freshness reader to validate ack files and guard their Git refs."""
    require_git_history(root, *revisions)
    try:
        freshness._store_neutral_ack_commits()
    except subprocess.CalledProcessError as exc:
        command = exc.cmd
        if not isinstance(command, (list, tuple)) or not command:
            raise
        revision = command[-1]
        suffix = "^{commit}"
        if not isinstance(revision, str) or not revision.endswith(suffix):
            raise
        require_git_history(root, *revisions, revision[: -len(suffix)])
        raise
