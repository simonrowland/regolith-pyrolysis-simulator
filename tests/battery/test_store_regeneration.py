"""Pins successful migration status and byte-level store verification."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

import simulator.battery.migrate as migration


def test_freshness_includes_the_migration_queue_output() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_store_freshness.py"
    spec = importlib.util.spec_from_file_location("check_store_freshness", script)
    assert spec is not None and spec.loader is not None
    freshness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(freshness)

    assert "data/battery/migration-queue.yaml" in freshness.STORE_OUTPUTS


def _empty_root(root: Path) -> Path:
    literature = root / "data" / "literature"
    literature.mkdir(parents=True)
    (literature / "INDEX.yaml").write_text(
        "schema_version: literature_index.v1\nsources: []\n",
        encoding="utf-8",
    )
    return root


def test_cli_returns_success_with_reported_baseline_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Known hard issues remain visible in the report, not a process failure."""
    result = SimpleNamespace(
        source_counts={},
        observations={},
        works={},
        experiments={},
        queue=(),
        validation=SimpleNamespace(hard_issues=(object(),) * 1402),
    )
    monkeypatch.setattr(migration, "migrate", lambda root, *, write: result)

    status = migration.main(["--dry-run", "--root", str(tmp_path)])

    assert status == 0
    assert "hard_issues=1402" in capsys.readouterr().out


def test_check_store_detects_byte_drift_in_a_small_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _empty_root(tmp_path / "repo")
    assert migration.main(["--root", str(root)]) == 0

    assert migration.main(["--check-store", "--root", str(root)]) == 0
    assert "byte-identical" in capsys.readouterr().out

    report = root / "data" / "battery" / "migration-report.md"
    report.write_text(report.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

    assert migration.main(["--check-store", "--root", str(root)]) == 1
    assert "migration-report.md" in capsys.readouterr().out


def test_reference_queue_report_is_stable_in_fresh_regeneration(
    tmp_path: Path,
) -> None:
    root = _empty_root(tmp_path / "repo")
    manifest = (
        root
        / "data"
        / "literature"
        / "compilations"
        / "fixture-reference"
        / "manifest.yaml"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "schema_version: literature_compilation_manifest.v1\n"
        "source_id: fixture-reference\n"
        "compilation_role:\n"
        "  scoring_eligible: false\n",
        encoding="utf-8",
    )
    result = migration.MigrationResult()
    result.add_queue(
        "fixture-work",
        {"page": 1},
        ["value"],
        "mapping required",
        source=(
            "data/literature/compilations/fixture-reference/records/record.yaml"
        ),
        observation_id="fixture-observation",
    )

    migration.write_outputs(result, root)

    assert result.queue_work_counts is not None
    assert result.queue_work_counts.reference_by_source == {"fixture-reference": 1}
    assert migration._store_regeneration_differences(root, result) == []


@pytest.mark.nightly
@pytest.mark.xdist_group("serial")
@pytest.mark.cpu_budget(2600)
def test_committed_store_is_byte_identical_to_regeneration() -> None:
    """CI must verify the real committed store against the committed inputs."""
    repo_root = Path(__file__).resolve().parents[2]
    assert migration.main(["--check-store", "--root", str(repo_root)]) == 0
