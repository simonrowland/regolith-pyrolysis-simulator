from __future__ import annotations

import copy
import importlib.util
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
                "readers": ["tests/example.py"],
            }
        ],
    )
    monkeypatch.setenv(_STRICT_ENV, "1")

    with pytest.raises(pytest.UsageError) as error:
        _invoke_sessionstart(tmp_path, monkeypatch)

    assert "docs-private/missing-input.yaml" in str(error.value)
    assert "tests/example.py" in str(error.value)


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
                "readers": ["tests/example.py"],
            },
            {
                "kind": "directory",
                "path": "docs-private/directory",
                "readers": ["tests/example.py"],
            },
            {
                "kind": "glob",
                "path": "docs-private/items/*.yaml",
                "readers": ["tests/example.py"],
                "required": [
                    {"path": member, "readers": ["tests/example.py"]}
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
                "readers": ["tests/example.py"],
            }
        ],
    )
    monkeypatch.delenv(_STRICT_ENV, raising=False)

    _invoke_sessionstart(tmp_path, monkeypatch)


def test_sessionstart_none_skips_docs_private_work_without_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_STRICT_ENV, raising=False)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw-docs-private-preflight")
    swept_roots: list[Path] = []
    monkeypatch.setattr(
        project_conftest,
        "_cleanup_dead_worker_caches",
        lambda root: swept_roots.append(root),
    )

    def unexpected_docs_private_work(*_args: object) -> None:
        raise AssertionError("strict docs-private setup ran without strict mode")

    monkeypatch.setattr(
        project_conftest,
        "_preflight_docs_private_inputs",
        unexpected_docs_private_work,
    )
    monkeypatch.setattr(
        project_conftest,
        "_install_docs_private_open_audit",
        unexpected_docs_private_work,
    )

    project_conftest.pytest_sessionstart(None)

    assert swept_roots == [project_conftest._worker_cache_root()]


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


def _docs_private_generator_fixture(root: Path):
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (root / "conftest.py").write_text(
        'DOCS_PRIVATE_MANIFEST_STRICT_ENV = '
        '"REGOLITH_DOCS_PRIVATE_MANIFEST_STRICT"\n',
        encoding="utf-8",
    )
    (tests_dir / "test_c6_grind_manifests.py").write_text(
        textwrap.dedent(
            '''
            from pathlib import Path

            ROOT = Path(__file__).resolve().parents[1]
            GRIND_ROOT = ROOT / "docs-private" / "grind"
            LITERATURE_ROOT = ROOT / "docs-private" / "deep-research" / "literature"
            MANIFEST_PATH = GRIND_ROOT / "manifest-c6-alpha.json"
            MANIFEST_PATH.read_text(encoding="utf-8")

            def read_inputs():
                list(GRIND_ROOT.glob("manifest-c6-*.json"))
                list(LITERATURE_ROOT.glob("*/benchmark-fixture.yaml"))
                job["profile"]
            '''
        ),
        encoding="utf-8",
    )
    epoch_dir = root / "scripts"
    epoch_dir.mkdir()
    (epoch_dir / "epoch_grind.py").write_text(
        "def load(job, profile_path):\n"
        "    profile = job.profile\n"
        "    _load_mapping(profile_path)\n"
        "    _file_sha256(profile_path)\n",
        encoding="utf-8",
    )

    grind_dir = root / "docs-private" / "grind"
    profiles_dir = grind_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    manifest_path = grind_dir / "manifest-c6-alpha.json"
    manifest_path.write_text(
        json.dumps({"jobs": [{"profile": "profiles/alpha.yaml"}]}),
        encoding="utf-8",
    )
    (profiles_dir / "alpha.yaml").write_text("profile: alpha\n", encoding="utf-8")
    literature_dir = root / "docs-private" / "deep-research" / "literature"
    for paper in ("paper-one", "paper-two"):
        paper_dir = literature_dir / paper
        paper_dir.mkdir(parents=True)
        (paper_dir / "benchmark-fixture.yaml").write_text(
            "fixture: true\n", encoding="utf-8"
        )

    committed_manifest = tests_dir / "docs_private_inputs.json"
    committed_manifest.write_text(
        json.dumps({"schema_version": 1, "entries": []}), encoding="utf-8"
    )
    generator_path = _REPO_ROOT / "scripts" / "derive_docs_private_inputs.py"
    spec = importlib.util.spec_from_file_location(
        "derive_docs_private_inputs_for_test", generator_path
    )
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    return generator, committed_manifest


def test_docs_private_generator_ignores_reader_line_shifts(
    tmp_path: Path,
) -> None:
    generator, _committed_manifest = _docs_private_generator_fixture(tmp_path)
    generated_before = generator.derive_manifest(tmp_path)

    reader_path = tmp_path / "tests" / "test_c6_grind_manifests.py"
    reader_path.write_text(
        "\n" + reader_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    assert generator.derive_manifest(tmp_path) == generated_before


def test_docs_private_generator_ignores_manifest_being_generated(
    tmp_path: Path,
) -> None:
    root = tmp_path
    generator, committed_manifest = _docs_private_generator_fixture(root)

    generated_before = generator.derive_manifest(root)
    profile_path = "docs-private/grind/profiles/alpha.yaml"
    corpus_entry = next(
        entry
        for entry in generated_before["entries"]
        if entry["kind"] == "glob"
        and entry["path"].startswith("docs-private/deep-research/literature/")
    )
    removed_corpus_path = corpus_entry["required"][0]["path"]
    mutated_manifest = copy.deepcopy(generated_before)
    mutated_manifest["entries"] = [
        entry
        for entry in mutated_manifest["entries"]
        if entry.get("path") != profile_path
    ]
    mutated_corpus = next(
        entry
        for entry in mutated_manifest["entries"]
        if entry["kind"] == "glob"
        and entry["path"].startswith("docs-private/deep-research/literature/")
    )
    mutated_corpus["required"] = [
        entry
        for entry in mutated_corpus["required"]
        if entry["path"] != removed_corpus_path
    ]
    mutated_manifest["entries"].extend(
        [
            {
                "kind": "file",
                "path": "docs-private/grind/profiles/invented.yaml",
                "readers": ["tests/example.py"],
            },
            {
                "kind": "file",
                "path": "docs-private/invented-reader.yaml",
                "readers": ["tests/example.py"],
            },
        ]
    )
    mutated_corpus["required"].append(
        {
            "path": "docs-private/deep-research/literature/invented/benchmark-fixture.yaml",
            "readers": ["tests/example.py"],
        }
    )
    committed_manifest.write_text(
        json.dumps(mutated_manifest), encoding="utf-8"
    )

    generated_after = generator.derive_manifest(root)

    assert generated_after == generated_before
    generated_paths = {
        entry["path"]
        for entry in generated_after["entries"]
    }
    assert profile_path in generated_paths
    generated_corpus_paths = {
        item["path"]
        for entry in generated_after["entries"]
        if entry["kind"] == "glob"
        and entry["path"].startswith("docs-private/deep-research/literature/")
        for item in entry["required"]
    }
    assert removed_corpus_path in generated_corpus_paths


def test_strict_audit_fails_session_for_missing_docs_private_open(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs-private").mkdir()
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    worker_script = textwrap.dedent(
        """
        import os
        import sys
        from pathlib import Path
        from types import SimpleNamespace

        import pytest
        import conftest

        root = Path(sys.argv[1]).resolve()
        assert conftest._install_docs_private_open_audit(root)
        protocol = conftest.pytest_runtest_protocol(
            SimpleNamespace(nodeid="tests/example.py::test_worker_missing_private_open"), None
        )
        next(protocol)
        try:
            try:
                (root / "docs-private" / "worker-missing.yaml").open("r", encoding="utf-8")
            except FileNotFoundError:
                pass
        finally:
            protocol.close()
        session = SimpleNamespace(exitstatus=pytest.ExitCode.OK)
        assert conftest._fail_on_missing_docs_private_opens(session) is None
        """
    )
    script = textwrap.dedent(
        """
        import os
        import subprocess
        import sys
        from pathlib import Path
        from types import SimpleNamespace

        import pytest
        import conftest

        root = Path(sys.argv[1]).resolve()
        repo_root = Path(sys.argv[3])
        assert conftest._install_docs_private_open_audit(root)
        def open_missing(name, nodeid):
            missing = root / "docs-private" / name
            protocol = conftest.pytest_runtest_protocol(
                SimpleNamespace(nodeid=nodeid), None
            )
            next(protocol)
            try:
                try:
                    missing.open("r", encoding="utf-8")
                except FileNotFoundError:
                    pass
            finally:
                protocol.close()

        open_missing(
            "controller-missing.yaml",
            "tests/example.py::test_controller_missing_private_open",
        )
        worker_env = os.environ.copy()
        worker_env["PYTEST_XDIST_WORKER"] = "gw-simulated"
        worker = subprocess.run(
            [sys.executable, "-c", sys.argv[2], str(root)],
            cwd=repo_root,
            env=worker_env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert worker.returncode == 0, worker.stderr
        audit_dir = Path(os.environ[conftest.DOCS_PRIVATE_AUDIT_DIR_ENV])
        (audit_dir / "truncated.jsonl").write_text('{"path":', encoding="utf-8")

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
    for name in tuple(audit_env):
        if name.startswith("PYTEST_XDIST_"):
            audit_env.pop(name)
    audit_env[_STRICT_ENV] = "1"
    audit_env[project_conftest.DOCS_PRIVATE_AUDIT_DIR_ENV] = str(audit_dir)
    audit_env["TMPDIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), worker_script, str(_REPO_ROOT)],
        cwd=_REPO_ROOT,
        env=audit_env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "missing docs-private inputs" in result.stderr
    assert "docs-private/controller-missing.yaml" in result.stderr
    assert "tests/example.py::test_controller_missing_private_open" in result.stderr
    assert "docs-private/worker-missing.yaml" in result.stderr
    assert "tests/example.py::test_worker_missing_private_open" in result.stderr
    assert "truncated audit record in truncated.jsonl" in result.stderr
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
