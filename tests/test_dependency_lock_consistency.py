"""Keep project dependency pins aligned with the checked-in uv lock."""

from __future__ import annotations

import re
import tomllib
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
_DIRECT_GIT_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[^\]]+\])?\s*@\s*(?P<url>git\+\S+)"
)


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _project_requirements(project: dict) -> list[str]:
    requirements = list(project.get("dependencies", []))
    for extra_requirements in project.get("optional-dependencies", {}).values():
        requirements.extend(extra_requirements)
    return requirements


def _declared_git_pins(
    requirements: list[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    pins = []
    for requirement, origin in requirements:
        match = _DIRECT_GIT_REQUIREMENT.match(requirement)
        if not match:
            continue
        path = urlsplit(match.group("url")[4:]).path
        if "@" not in path:
            continue
        revision = path.rsplit("@", 1)[1]
        if revision:
            pins.append((_canonical_name(match.group("name")), revision, origin))
    return pins


def _locked_git_revision(source: dict) -> str | None:
    git_url = source.get("git")
    if not git_url:
        return None
    parsed = urlsplit(git_url)
    query_revision = parse_qs(parsed.query).get("rev")
    if query_revision:
        return query_revision[0]
    if "@" in parsed.path:
        return parsed.path.rsplit("@", 1)[1]
    return parsed.fragment or None


def _assert_git_dependency_revisions_match(
    project: dict, lock: dict, requirements_root: Path = ROOT
) -> None:
    locked_git = defaultdict(list)
    for package in lock.get("package", []):
        source = package.get("source", {})
        if source.get("git"):
            locked_git[_canonical_name(package["name"])].append(
                _locked_git_revision(source)
            )

    mismatches = []
    declarations = _all_declared_requirements(project, requirements_root)
    for name, declared_revision, origin in _declared_git_pins(declarations):
        locked_revisions = locked_git.get(name, [])
        if not locked_revisions:
            mismatches.append(
                f"{origin}: {name}: declared rev {declared_revision}; "
                "uv.lock rev <missing>"
            )
            continue
        for locked_revision in locked_revisions:
            if locked_revision != declared_revision:
                mismatches.append(
                    f"{origin}: {name}: declared rev {declared_revision}; "
                    f"uv.lock rev {locked_revision or '<missing>'}"
                )

    assert not mismatches, "Git dependency revision mismatch:\n" + "\n".join(
        mismatches
    )


def test_git_dependency_revisions_match_uv_lock() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)["project"]
    with (ROOT / "uv.lock").open("rb") as lock_file:
        lock = tomllib.load(lock_file)

    _assert_git_dependency_revisions_match(project, lock)


def _all_declared_requirements(
    project: dict, requirements_root: Path = ROOT
) -> list[tuple[str, str]]:
    requirements = [
        (requirement, "pyproject.toml")
        for requirement in _project_requirements(project)
    ]
    for requirements_file in sorted(requirements_root.glob("requirements*.txt")):
        for line_number, raw_line in enumerate(
            requirements_file.read_text().splitlines(), start=1
        ):
            requirement = re.split(r"\s+#", raw_line, maxsplit=1)[0].strip()
            if requirement and not requirement.startswith("-"):
                requirements.append(
                    (
                        requirement,
                        f"{requirements_file.name}:{line_number}",
                    )
                )
    return requirements


def test_requirements_file_git_revision_mismatch_is_rejected(tmp_path: Path) -> None:
    requirements_file = tmp_path / "requirements.txt"
    requirements_file.write_text(
        "synthetic-dep @ git+https://example.com/synthetic-dep@declared-rev\n"
    )
    lock = tomllib.loads(
        """
        [[package]]
        name = "synthetic-dep"
        version = "1.0.0"
        source = { git = "https://example.com/synthetic-dep?rev=locked-rev#locked-rev" }
        """
    )

    with pytest.raises(AssertionError) as error:
        _assert_git_dependency_revisions_match(
            {"dependencies": [], "optional-dependencies": {}}, lock, tmp_path
        )

    message = str(error.value)
    assert "requirements.txt:1" in message
    assert "synthetic-dep" in message
    assert "declared-rev" in message
    assert "locked-rev" in message


def test_declared_version_requirements_match_uv_lock() -> None:
    try:
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import canonicalize_name
        from packaging.version import Version
    except ImportError as error:
        pytest.skip(f"version pin check skipped: packaging is not available ({error})")

    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)["project"]
    with (ROOT / "uv.lock").open("rb") as lock_file:
        lock = tomllib.load(lock_file)

    locked_versions = defaultdict(list)
    for package in lock.get("package", []):
        locked_versions[canonicalize_name(package["name"])].append(
            package["version"]
        )

    mismatches = []
    for raw_requirement, origin in _all_declared_requirements(project):
        requirement = Requirement(raw_requirement)
        if not any(
            specifier.operator in {">=", "=="}
            for specifier in requirement.specifier
        ):
            continue
        name = canonicalize_name(requirement.name)
        versions = locked_versions.get(name, [])
        if not versions:
            mismatches.append(f"{origin}: {name}{requirement.specifier} is not locked")
            continue
        specifiers = SpecifierSet(str(requirement.specifier))
        incompatible = [
            version for version in versions if Version(version) not in specifiers
        ]
        if incompatible:
            mismatches.append(
                f"{origin}: {name}{requirement.specifier}; "
                f"uv.lock versions {', '.join(incompatible)}"
            )

    assert not mismatches, "Dependency version mismatch:\n" + "\n".join(mismatches)
