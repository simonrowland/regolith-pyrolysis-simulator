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


def store_neutral_ack_revisions(root: Path) -> tuple[str, ...]:
    """Return valid revision names from store-neutral acknowledgement files."""
    ack_dir = root / "scripts" / "store_neutral_acks"
    ack_revisions = []
    if ack_dir.is_dir():
        for path in sorted(ack_dir.iterdir()):
            stem = path.stem
            valid_length = 7 <= len(stem) <= 12 or len(stem) == 40
            if (
                path.suffix == ".txt"
                and valid_length
                and all(char in "0123456789abcdefABCDEF" for char in stem)
            ):
                ack_revisions.append(stem)
    return tuple(ack_revisions)
