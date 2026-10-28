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
        # Pip files allow blank lines, full-line comments (including indentation),
        # and inline comments introduced by whitespace before '#'. Join '\\'
        # continuations before interpreting declarations. Skip requirement-file
        # directives (-r/--requirement, -c/--constraint), editable installs
        # (-e/--editable), and other option lines such as --index-url,
        # --extra-index-url, --find-links, and --trusted-host: these configure pip
        # but do not declare a dependency pin.
        logical_requirement = ""
        logical_start_line = None
        for line_number, raw_line in enumerate(
            requirements_file.read_text().splitlines(), start=1
        ):
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            uncommented = re.sub(r"\s+#.*$", "", raw_line)
            if not uncommented.strip():
                continue

            if logical_start_line is None:
                logical_start_line = line_number
            piece = uncommented.lstrip()
            stripped_piece = piece.rstrip()
            continued = stripped_piece.endswith("\\")
            if continued:
                piece = piece[: len(stripped_piece) - 1]
            logical_requirement += piece
            if continued:
                continue

            requirement = logical_requirement.strip()
            if requirement and not requirement.startswith("-"):
                requirements.append(
                    (
                        requirement,
                        f"{requirements_file.name}:{logical_start_line}",
                    )
                )
            logical_requirement = ""
            logical_start_line = None

        if logical_start_line is not None:
            requirement = logical_requirement.strip()
            if requirement and not requirement.startswith("-"):
                requirements.append(
                    (
                        requirement,
                        f"{requirements_file.name}:{logical_start_line}",
                    )
                )
    return requirements


def test_requirements_file_git_revision_mismatch_is_rejected(tmp_path: Path) -> None:
    requirements_file = tmp_path / "requirements.txt"
    requirements_text = (
        "    # PetThermoTools, Thermobar, PySulfSat and VapoRock are installed\n"
        "\n"
        "--index-url https://example.com/simple\n"
        "-r other-requirements.txt\n"
        "-c constraints.txt\n"
        "-e .\n"
        "synthetic-dep @ git+https://example.com/synthetic-dep@declared-\\\n"
        "    rev # continued revision\n"
    )
    requirements_file.write_text(requirements_text)
    requirement_start_line = next(
        line_number
        for line_number, line in enumerate(requirements_text.splitlines(), start=1)
        if line.startswith("synthetic-dep @ ")
    )
    lock = tomllib.loads(
        """
        [[package]]
        name = "synthetic-dep"
        version = "1.0.0"
        source = { git = "https://example.com/synthetic-dep?rev=locked-rev#locked-rev" }
        """
    )
    project = {"dependencies": [], "optional-dependencies": {}}

    assert _all_declared_requirements(project, tmp_path) == [
        (
            "synthetic-dep @ git+https://example.com/synthetic-dep@declared-rev",
            f"requirements.txt:{requirement_start_line}",
        )
    ]

    with pytest.raises(AssertionError) as error:
        _assert_git_dependency_revisions_match(
            project, lock, tmp_path
        )

    message = str(error.value)
    assert f"requirements.txt:{requirement_start_line}" in message
    assert "synthetic-dep" in message
    assert "declared-rev" in message
    assert "locked-rev" in message


def _assert_declared_versions_match_uv_lock(
    project: dict, lock: dict, requirements_root: Path = ROOT
) -> None:
    try:
        from packaging.requirements import InvalidRequirement, Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import canonicalize_name
        from packaging.version import Version
    except ImportError as error:
        pytest.skip(f"version pin check skipped: packaging is not available ({error})")

    locked_versions = defaultdict(list)
    for package in lock.get("package", []):
        locked_versions[canonicalize_name(package["name"])].append(
            package["version"]
        )

    mismatches = []
    for raw_requirement, origin in _all_declared_requirements(
        project, requirements_root
    ):
        try:
            requirement = Requirement(raw_requirement)
        except InvalidRequirement as error:
            mismatches.append(
                f"{origin}: invalid requirement {raw_requirement!r}: {error}"
            )
            continue
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


def test_requirements_file_invalid_requirement_names_its_line(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("this is not a valid requirement\n")

    with pytest.raises(
        AssertionError, match=r"requirements\.txt:1: invalid requirement"
    ):
        _assert_declared_versions_match_uv_lock(
            {"dependencies": [], "optional-dependencies": {}},
            {"package": []},
            tmp_path,
        )


def test_declared_version_requirements_match_uv_lock() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)["project"]
    with (ROOT / "uv.lock").open("rb") as lock_file:
        lock = tomllib.load(lock_file)

    _assert_declared_versions_match_uv_lock(project, lock)
