from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import conftest as project_conftest


_STRICT_ENV = "REGOLITH_DOCS_PRIVATE_MANIFEST_STRICT"


def _write_manifest(root: Path, entries: list[dict[str, object]]) -> None:
    manifest_path = root / "tests" / "docs_private_inputs.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "entries": entries}), encoding="utf-8"
    )


def _invoke_sessionstart(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw-docs-private-preflight")
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
    member = "docs-private/items/present.yaml"
    member_path = tmp_path / member
    member_path.parent.mkdir(parents=True)
    member_path.write_text("present\n", encoding="utf-8")
    _write_manifest(
        tmp_path,
        [
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
