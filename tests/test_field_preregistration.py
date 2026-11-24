from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import simulator.field_preregistration as field_preregistration
from simulator.diagnostic_helpers.reproduction_compare import content_digest


def _freeze(tmp_path: Path, *, version: int = 1, predecessor_digest: str | None = None):
    return field_preregistration._freeze_field_run(
        "robinot-example",
        setup={"sample": {"mass_g": None}, "example_only": True},
        resolved={
            "sample": {"mass_g": 3.38, "exposed_area_m2": None},
            "example_only": True,
        },
        engine_commit="example-engine-commit",
        provider_runtime_identity={"provider": "example-provider"},
        predicted_series={
            "o2_cumulative_kg": [],
            "o2_window_kg": [],
            "wall_deposit_committed_kg": [],
            "melt_inventory_snapshots": [],
        },
        flags=[{"kind": "example_only", "reason": "not a real field run"}],
        root=tmp_path / "data" / "field-preregistration",
        version=version,
        predecessor_digest=predecessor_digest,
    )


def test_freeze_writes_canonical_bundle_and_preserves_source_nulls(
    tmp_path: Path,
) -> None:
    directory = _freeze(tmp_path)

    setup = yaml.safe_load((directory / "setup.yaml").read_text(encoding="utf-8"))
    resolved = yaml.safe_load(
        (directory / "resolved.yaml").read_text(encoding="utf-8")
    )
    bundle = json.loads((directory / "bundle.json").read_text(encoding="utf-8"))
    digest = (directory / "bundle.sha256").read_text(encoding="utf-8").strip()

    assert setup["sample"]["mass_g"] is None
    assert resolved["sample"]["exposed_area_m2"] is None
    assert bundle["setup_digest"] == content_digest(setup)
    assert bundle["resolved_digest"] == content_digest(resolved)
    assert bundle["predicted_series"]["o2_cumulative_kg"] == []
    assert digest == content_digest(bundle)


def test_freeze_refuses_v1_rewrite_and_links_v2_to_predecessor(
    tmp_path: Path,
) -> None:
    version_one = _freeze(tmp_path)
    predecessor = (version_one / "bundle.sha256").read_text(encoding="utf-8").strip()

    with pytest.raises(FileExistsError):
        _freeze(tmp_path)

    version_two = field_preregistration._freeze_field_run(
        "robinot-example",
        setup={"sample": {"mass_g": 3.38}, "example_only": True},
        resolved={"sample": {"mass_g": 3.38}, "example_only": True},
        engine_commit="example-engine-commit-2",
        provider_runtime_identity={"provider": "example-provider"},
        predicted_series={"o2_cumulative_kg": []},
        flags=[{"kind": "example_only"}],
        root=tmp_path / "data" / "field-preregistration",
        version=2,
        predecessor_digest=predecessor,
    )
    bundle = json.loads((version_two / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["predecessor_digest"] == predecessor
    assert bundle["version"] == 2


def test_immutability_test_detects_overwrite_guard_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(tmp_path)
    monkeypatch.setattr(
        field_preregistration,
        "_create_version_directory",
        lambda directory: directory.mkdir(parents=True, exist_ok=True),
    )

    def assert_rewrite_refused() -> None:
        try:
            _freeze(tmp_path)
        except FileExistsError:
            return
        raise AssertionError("mutated freeze guard allowed v1 overwrite")

    with pytest.raises(AssertionError, match="allowed v1 overwrite"):
        assert_rewrite_refused()
