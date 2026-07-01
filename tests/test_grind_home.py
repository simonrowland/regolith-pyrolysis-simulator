from __future__ import annotations

from pathlib import Path

import pytest

from scripts.grind_home import campaign_dir, campaign_path, resolve_grind_home


def test_resolve_grind_home_uses_env_override() -> None:
    assert resolve_grind_home({"GRIND_HOME": "~/custom-grind"}) == Path("~/custom-grind").expanduser().resolve(
        strict=False
    )


def test_resolve_grind_home_defaults_to_regolith_grind() -> None:
    assert resolve_grind_home({}) == Path("~/regolith-grind").expanduser().resolve(strict=False)


def test_resolve_grind_home_rejects_relative_env_override() -> None:
    with pytest.raises(ValueError):
        resolve_grind_home({"GRIND_HOME": "../repo"})


def test_campaign_dir_rejects_relative_explicit_grind_home() -> None:
    with pytest.raises(ValueError):
        campaign_dir("C2A_continuous", grind_home="../repo")


def test_campaign_paths_stay_under_grind_home() -> None:
    root = Path("/tmp/grind-root")
    normalized_root = root.resolve(strict=False)

    assert campaign_dir("C2A_continuous", grind_home=root) == normalized_root / "campaigns" / "C2A_continuous"
    assert campaign_path("C2A_continuous", "epochs", "run-001", grind_home=root) == (
        normalized_root / "campaigns" / "C2A_continuous" / "epochs" / "run-001"
    )


@pytest.mark.parametrize("bad_campaign", ["", ".", "..", "../repo", "nested/name", "nested\\name"])
def test_campaign_name_rejects_path_traversal(bad_campaign: str) -> None:
    with pytest.raises(ValueError):
        campaign_dir(bad_campaign, grind_home="/tmp/grind-root")


@pytest.mark.parametrize("bad_part", ["", ".", "..", "../repo", "/tmp/repo", "nested/name", "nested\\name"])
def test_campaign_path_parts_reject_path_traversal(bad_part: str) -> None:
    with pytest.raises(ValueError):
        campaign_path("C2A_continuous", bad_part, grind_home="/tmp/grind-root")
