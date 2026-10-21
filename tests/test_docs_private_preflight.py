from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

import conftest as project_conftest


_STRICT_ENV = "REGOLITH_DOCS_PRIVATE_MANIFEST_STRICT"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_manifest(root: Path, entries: list[dict[str, object]]) -> None:
    manifest_path = root / "tests" / "docs_private_inputs.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "entries": entries}), encoding="utf-8"
    )


def _invoke_sessionstart(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw-docs-private-preflight")
    monkeypatch.setattr(
        project_conftest, "_install_docs_private_open_audit", lambda _root: False
    )
    session = SimpleNamespace(config=SimpleNamespace(rootpath=root))
    project_conftest.pytest_sessionstart(session)


def test_strict_docs_private_preflight_errors_with_missing_manifest_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(
        tmp_path,
        [
            {
                "kind": "file",
                "path": "docs-private/missing-input.yaml",
                "reader": "tests/example.py:42",
            }
        ],
    )
    monkeypatch.setenv(_STRICT_ENV, "1")

    with pytest.raises(pytest.UsageError) as error:
        _invoke_sessionstart(tmp_path, monkeypatch)

    assert "docs-private/missing-input.yaml" in str(error.value)
    assert "tests/example.py:42" in str(error.value)


def test_strict_docs_private_preflight_accepts_present_glob_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    direct_file = tmp_path / "docs-private" / "direct.yaml"
    direct_file.parent.mkdir(parents=True)
    direct_file.write_text("present\n", encoding="utf-8")
    required_directory = tmp_path / "docs-private" / "directory"
    required_directory.mkdir()
    member = "docs-private/items/present.yaml"
    member_path = tmp_path / member
    member_path.parent.mkdir(parents=True)
    member_path.write_text("present\n", encoding="utf-8")
    _write_manifest(
        tmp_path,
        [
            {
                "kind": "file",
                "path": "docs-private/direct.yaml",
                "reader": "tests/example.py:5",
            },
            {
                "kind": "directory",
                "path": "docs-private/directory",
                "reader": "tests/example.py:7",
            },
            {
                "kind": "glob",
                "path": "docs-private/items/*.yaml",
                "reader": "tests/example.py:10",
                "required": [
                    {"path": member, "reader": "tests/example.py:11"}
                ],
            }
        ],
    )
    monkeypatch.setenv(_STRICT_ENV, "1")

    _invoke_sessionstart(tmp_path, monkeypatch)


def test_docs_private_preflight_is_inert_without_strict_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(
        tmp_path,
        [
            {
                "kind": "file",
                "path": "docs-private/missing-input.yaml",
                "reader": "tests/example.py:42",
            }
        ],
    )
    monkeypatch.delenv(_STRICT_ENV, raising=False)

    _invoke_sessionstart(tmp_path, monkeypatch)


def test_docs_private_manifest_matches_reader_generator() -> None:
    if not (_REPO_ROOT / "docs-private").is_dir() and _STRICT_ENV not in os.environ:
        pytest.skip("manifest expansion needs the docs-private source tree")

    generated = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "derive_docs_private_inputs.py")],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    committed = json.loads(
        (_REPO_ROOT / "tests" / "docs_private_inputs.json").read_text(
            encoding="utf-8"
        )
    )
    generated_manifest = json.loads(generated.stdout)
    assert generated_manifest == committed

    deleted_profile = copy.deepcopy(committed)
    profile_entry = next(
        entry
        for entry in deleted_profile["entries"]
        if entry["kind"] == "file"
        and entry["path"].startswith("docs-private/grind/profiles/")
    )
    deleted_profile["entries"].remove(profile_entry)
    assert deleted_profile != generated_manifest

    deleted_corpus_member = copy.deepcopy(committed)
    corpus_glob = next(
        entry
        for entry in deleted_corpus_member["entries"]
        if entry["kind"] == "glob"
        and entry["path"].startswith("docs-private/deep-research/literature/")
    )
    corpus_glob["required"].pop()
    assert deleted_corpus_member != generated_manifest

    invented_entry = copy.deepcopy(committed)
    invented_entry["entries"].append(
        {
            "kind": "file",
            "path": "docs-private/invented-reader.yaml",
            "reader": "tests/example.py:1",
        }
    )
    assert invented_entry != generated_manifest


def test_strict_audit_fails_session_for_missing_docs_private_open(
    tmp_path: Path,
) -> None:
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from types import SimpleNamespace

        import pytest
        import conftest

        root = Path(sys.argv[1])
        missing = root / "docs-private" / "missing-audit.yaml"
        assert conftest._install_docs_private_open_audit(root)
        protocol = conftest.pytest_runtest_protocol(
            SimpleNamespace(nodeid="tests/example.py::test_missing_private_open"), None
        )
        next(protocol)
        try:
            try:
                missing.open("r", encoding="utf-8")
            except FileNotFoundError:
                pass
        finally:
            protocol.close()

        session = SimpleNamespace(
            exitstatus=pytest.ExitCode.OK,
            config=SimpleNamespace(
                pluginmanager=SimpleNamespace(getplugin=lambda _name: None)
            ),
        )
        conftest.pytest_sessionfinish(session, pytest.ExitCode.OK)
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
        """
    )
    audit_env = os.environ.copy()
    audit_env[_STRICT_ENV] = "1"
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=_REPO_ROOT,
        env=audit_env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "missing docs-private inputs" in result.stderr
    assert "docs-private/missing-audit.yaml" in result.stderr
    assert "tests/example.py::test_missing_private_open" in result.stderr
    assert "rerun scripts/derive_docs_private_inputs.py" in result.stderr.lower()


def test_docs_private_open_audit_is_inert_without_strict_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_STRICT_ENV, raising=False)
    monkeypatch.setattr(project_conftest, "_DOCS_PRIVATE_AUDIT_ROOT", None)
    monkeypatch.setattr(project_conftest, "_DOCS_PRIVATE_AUDIT_INSTALLED", False)
    installed = False

    def record_install(_hook) -> None:
        nonlocal installed
        installed = True

    monkeypatch.setattr(project_conftest.sys, "addaudithook", record_install)

    assert project_conftest._install_docs_private_open_audit(tmp_path) is False
    assert installed is False
